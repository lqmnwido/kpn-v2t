import { Sha256 } from '@aws-crypto/sha256-browser';
import { SignatureV4 } from '@smithy/signature-v4';
import { editVttCues } from './media.js';

export const settings = {
  api: import.meta.env.VITE_ASR_API_URL || 'http://localhost:8081',
  minio: import.meta.env.VITE_MINIO_URL || 'http://localhost:9000',
  bucket: import.meta.env.VITE_MINIO_BUCKET || 'asr-poc',
  region: import.meta.env.VITE_MINIO_REGION || 'us-east-1',
};

export const credentials = {
  accessKeyId: import.meta.env.VITE_MINIO_ACCESS_KEY || '',
  secretAccessKey: import.meta.env.VITE_MINIO_SECRET_KEY || '',
  sessionToken: import.meta.env.VITE_MINIO_SESSION_TOKEN || undefined,
};

function requestError(message, status, data) {
  return Object.assign(new Error(message), { status, data });
}

export async function api(path, options) {
  const response = await fetch(settings.api.replace(/\/$/, '') + path, { cache: 'no-store', ...options });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    throw requestError(body?.detail || body?.message || body?.error || `API HTTP ${response.status}`, response.status, body);
  }
  return body;
}

export function startTranscription(job) {
  return api('/jobs/external', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ jobId: job.id, path: `${job.bucket}/${job.key}` }),
  });
}

function encodePart(value) {
  return encodeURIComponent(value).replace(/[!'()*]/g, char => '%' + char.charCodeAt(0).toString(16).toUpperCase());
}

function queryString(query) {
  const pairs = Object.entries(query).map(([name, value]) => encodePart(name) + '=' + encodePart(value));
  return pairs.length ? '?' + pairs.join('&') : '';
}

export function connectMinio(credentials) {
  const endpoint = new URL(settings.minio);
  if (!['http:', 'https:'].includes(endpoint.protocol) || endpoint.username || endpoint.password
      || endpoint.search || endpoint.hash || endpoint.pathname !== '/') {
    throw new Error('Gunakan URL API MinIO tanpa laluan, parameter atau kelayakan.');
  }
  const signer = new SignatureV4({
    service: 's3', region: settings.region, credentials, sha256: Sha256, uriEscapePath: false,
  });
  function objectPath(bucket, key) {
    return '/' + (key ? [bucket, ...key.split('/')] : [bucket]).map(encodePart).join('/');
  }
  async function objectRequest(method, bucket, key, { body, headers = {}, query = {} } = {}) {
    const path = objectPath(bucket, key);
    const signed = await signer.sign({
      method, path, protocol: endpoint.protocol, hostname: endpoint.hostname, query,
      headers: { ...headers, host: endpoint.host, 'x-amz-content-sha256': 'UNSIGNED-PAYLOAD' },
    });
    const { host, ...browserHeaders } = signed.headers;
    let response;
    try {
      response = await fetch(endpoint.origin + path + queryString(query), {
        method, headers: browserHeaders, body, redirect: 'error', cache: 'no-store',
      });
    } catch {
      throw new Error('MinIO tidak dapat dicapai. Semak URL dan tetapan CORS MinIO.');
    }
    if (!response.ok) {
      const detail = await response.text();
      const code = detail.match(/<Code>([^<]+)<\/Code>/)?.[1];
      throw requestError(code ? `MinIO: ${code}` : `MinIO HTTP ${response.status}`, response.status);
    }
    return response;
  }

  objectRequest.playbackUrl = async (bucket, key) => {
    const path = objectPath(bucket, key);
    const request = await signer.presign({
      method: 'GET', path, protocol: endpoint.protocol, hostname: endpoint.hostname,
      headers: { host: endpoint.host, 'X-Amz-Content-Sha256': 'UNSIGNED-PAYLOAD' },
      query: {},
    }, { expiresIn: 3600 });
    return endpoint.origin + path + queryString(request.query);
  };
  return objectRequest;
}

export async function readTranscript(storage, job) {
  const response = await storage('GET', job.bucket, `results/${job.id}/transcript.json`);
  const document = await response.json();
  if (typeof document?.text !== 'string') throw new Error('Fail transkrip tidak mempunyai medan teks.');
  return { document, etag: response.headers.get('etag') };
}

export async function saveTranscript(storage, job, review, text, additions, rowTimings, updates) {
  if (!review.etag) {
    throw new Error('MinIO perlu mendedahkan pengepala ETag melalui CORS untuk menyimpan suntingan.');
  }
  const prefix = `results/${job.id}/`;
  const headers = { 'content-type': 'application/json' };
  try {
    await storage('PUT', job.bucket, prefix + 'transcript.original.json', {
      body: JSON.stringify(review.document), headers: { ...headers, 'if-none-match': '*' },
    });
  } catch (error) {
    if (error.status !== 412) throw error;
  }
  const document = { ...review.document, text, studioRowTimings: rowTimings };
  if (updates.length) {
    const previous = Array.isArray(document.studioCaptionUpdates) ? document.studioCaptionUpdates : [];
    const byId = new Map(previous.map(edit => [edit.id, edit]));
    for (const edit of updates) {
      const previousEdit = byId.get(edit.id);
      byId.set(edit.id, {
        ...edit,
        before: previousEdit?.before ?? edit.before,
        beforeStart: previousEdit?.beforeStart ?? previousEdit?.start ?? edit.beforeStart,
        beforeEnd: previousEdit?.beforeEnd ?? previousEdit?.end ?? edit.beforeEnd,
        retimed: Boolean(previousEdit?.retimed || edit.retimed),
      });
    }
    document.studioCaptionUpdates = [...byId.values()];
  }
  if (additions.length || updates.some(edit => edit.retimed)) {
    const segments = (Array.isArray(document.segments) ? document.segments : []).map(segment => ({ ...segment }));
    for (const edit of updates.filter(edit => edit.retimed)) {
      const sameBounds = segments.filter(segment => (segment.startSeconds ?? segment.start) === edit.beforeStart
        && (segment.endSeconds ?? segment.end) === edit.beforeEnd);
      const segment = segments.find(segment => segment.studioCueId === edit.id)
        ?? (sameBounds.length === 1 ? sameBounds[0] : null);
      if (segment) Object.assign(segment, {
        start: edit.start, end: edit.end, startSeconds: edit.start, endSeconds: edit.end, studioCueId: edit.id,
      });
    }
    for (const cue of additions) {
      segments.push({
        speaker: cue.speaker, text: cue.text, start: cue.start, end: cue.end,
        startSeconds: cue.start, endSeconds: cue.end, studioCueId: cue.id,
      });
    }
    if (additions.length) {
      const savedCues = Array.isArray(document.studioTimedCues) ? document.studioTimedCues : [];
      document.studioTimedCues = [...savedCues, ...additions];
    }
    document.segments = segments.sort((a, b) => (a.startSeconds ?? a.start ?? 0) - (b.startSeconds ?? b.start ?? 0));
  }
  try {
    const response = await storage('PUT', job.bucket, prefix + 'transcript.json', {
      body: JSON.stringify(document), headers: { ...headers, 'if-match': review.etag },
    });
    return { document, etag: response.headers.get('etag') };
  } catch (error) {
    if (error.status === 412) {
      throw new Error('Transkrip telah berubah. Muat semula transkrip sebelum menyimpan suntingan.');
    }
    throw error;
  }
}

export async function readCaptionDocument(storage, job) {
  const key = `results/${job.id}/transcript.vtt`;
  try {
    const response = await storage('GET', job.bucket, key);
    return { key, text: await response.text(), etag: response.headers.get('etag'), exists: true };
  } catch (error) {
    if (error.status !== 404) throw error;
    return { key, text: 'WEBVTT\n\n', etag: null, exists: false };
  }
}

export async function saveTimedCaptions(storage, job, additions, updates) {
  const current = await readCaptionDocument(storage, job);
  const text = editVttCues(current.text, additions, updates);
  if (text === current.text) return;
  if (current.exists && !current.etag) {
    throw new Error('MinIO perlu mendedahkan pengepala ETag untuk menyimpan sari kata.');
  }
  try {
    await storage('PUT', job.bucket, current.key, {
      body: text,
      headers: {
        'content-type': 'text/vtt',
        ...(current.exists ? { 'if-match': current.etag } : { 'if-none-match': '*' }),
      },
    });
  } catch (error) {
    if (error.status === 412) throw new Error('Sari kata telah berubah. Cuba simpan semula.');
    throw error;
  }
}

async function listObjects(storage, bucket, prefix) {
  const keys = [];
  let continuation;
  do {
    const query = { 'list-type': '2', prefix, 'encoding-type': 'url' };
    if (continuation) query['continuation-token'] = continuation;
    const result = await storage('GET', bucket, '', { query });
    const xml = new DOMParser().parseFromString(await result.text(), 'application/xml');
    if (xml.documentElement.localName !== 'ListBucketResult') {
      throw new Error('MinIO tidak dapat menyenaraikan fail untuk rakaman ini.');
    }
    for (const item of xml.getElementsByTagName('Contents')) {
      const key = decodeURIComponent(item.getElementsByTagName('Key')[0].textContent);
      if (!key.startsWith(prefix) || key.split('/').some(part => part === '.' || part === '..')) {
        throw new Error('Laluan fail MinIO di luar skop pemadaman.');
      }
      keys.push(key);
    }
    const truncated = xml.getElementsByTagName('IsTruncated')[0]?.textContent === 'true';
    const next = xml.getElementsByTagName('NextContinuationToken')[0]?.textContent;
    if (truncated && (!next || next === continuation)) throw new Error('Senarai fail MinIO tidak lengkap.');
    continuation = truncated ? next : undefined;
  } while (continuation);
  return keys;
}

export async function deleteRecording(storage, job) {
  if (!/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i.test(job.id)) {
    throw new Error('ID rakaman tidak sah.');
  }
  const prefixes = ['results', 'kpn', 'chunks', 'normalized', 'uploads'];
  const lists = await Promise.all(prefixes.map(prefix => listObjects(storage, job.bucket, `${prefix}/${job.id}/`)));
  for (const keys of lists) {
    for (let i = 0; i < keys.length; i += 8) {
      const batch = await Promise.allSettled(keys.slice(i, i + 8).map(key => storage('DELETE', job.bucket, key)));
      const failure = batch.find(result => result.status === 'rejected' && result.reason.status !== 404);
      if (failure) throw failure.reason;
    }
  }
}

export function validateReportTemplate(file) {
  const name = file.name.trim();
  if (name.length <= 5 || name.length > 160 || !/\.docx$/i.test(name) || /[\u0000-\u001f\u007f-\u009f/\\]/.test(name)) {
    throw new Error('Pilih fail templat Word (.docx) dengan nama yang sah.');
  }
  if (!file.size || file.size > 5 * 1024 * 1024) {
    throw new Error('Fail templat Word mesti antara 1 bait dan 5 MiB.');
  }
  return name;
}

export async function uploadReportTemplate(storage, job, file) {
  const name = validateReportTemplate(file);
  const id = crypto.randomUUID();
  const key = `results/${job.id}/kpn/templates/${id}/${name}`;
  await storage('PUT', job.bucket, key, {
    body: file,
    headers: {
      'content-type': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      'if-none-match': '*',
    },
  });
  return { id, name };
}

export async function downloadReport(storage, job, report) {
  const response = await storage('GET', report.bucket || job.bucket, report.outputKey);
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement('a');
  link.href = url;
  link.download = report.outputKey.split('/').at(-1) || 'Rapporteur.docx';
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
