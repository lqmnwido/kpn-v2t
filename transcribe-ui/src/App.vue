<script setup>
import ReportProgress from './ReportProgress.vue';
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, shallowRef, watch } from 'vue';
import { api, connectMinio, credentials, deleteRecording, downloadReport, readCaptionDocument, readTranscript, saveTimedCaptions, saveTranscript, settings, startTranscription, uploadReportTemplate, validateReportTemplate } from './services.js';
import { editVttCues, formatTime, formatVttTime, inspectMedia, mediaKind, parseVtt } from './media.js';
import { assignRowTimes, cleanSpeaker, renameSpeaker, rowTimings, sortTranscriptRows, splitSpeaker, transcriptRows, transcriptText } from './transcript.js';

const queueKey = 'transcribe-ui-queue';
const finished = new Set(['COMPLETED', 'FAILED', 'CANCELED', 'CANCELLED', 'TERMINATED', 'TIMED_OUT']);
const statusText = {
  CREATED: 'Dalam giliran', STARTED: 'Dalam giliran', PROCESSING: 'Sedang ditranskripsi',
  RUNNING: 'Sedang dijana', COMPLETED: 'Selesai', FAILED: 'Gagal', CANCELED: 'Dibatalkan',
  CANCELLED: 'Dibatalkan', TERMINATED: 'Ditamatkan', TIMED_OUT: 'Masa tamat',
};

function readQueue() {
  try {
    const items = JSON.parse(localStorage.getItem(queueKey) || '[]');
    return Array.isArray(items) ? items : [];
  } catch {
    return [];
  }
}
const jobs = ref(readQueue());
const selectedId = ref(null);
const current = computed(() => jobs.value.find(job => job.id === selectedId.value));
const selectedSpeaker = ref('');
const templateDragging = ref(false);
const speakerName = ref('');
const timedEntryOpen = ref(false);
const timingRow = ref(null);
const timingForm = ref(null);
const timedEntryError = ref('');
const captureField = ref(null);
let captureBefore;
const playing = ref(false);
const timedEntry = reactive({ speaker: '', text: '', start: 0, end: 3 });
const timeFields = [{ field: 'start', label: 'Masa mula' }, { field: 'end', label: 'Masa tamat' }];
const captionGap = 0.001;

function resetTimedEntry() {
  Object.assign(timedEntry, { speaker: '', text: '', start: 0, end: 3 });
  timedEntryError.value = '';
  timedEntryOpen.value = false;
  timingRow.value = null;
  captureField.value = null;
  captureBefore = null;
}
const speakers = computed(() => [...new Set((current.value?.rows || [])
  .map(row => cleanSpeaker(row.speaker)).filter(Boolean))]);
const hasTranscriptText = computed(() => (current.value?.rows || []).some(row => row.text.trim()));
watch(selectedId, () => {
  selectedSpeaker.value = '';
  speakerName.value = '';
  templateDragging.value = false;
  resetTimedEntry();
});

function applySpeakerName() {
  if (!current.value) return;
  const name = cleanSpeaker(speakerName.value);
  renameSpeaker(current.value.rows, selectedSpeaker.value, name);
  selectedSpeaker.value = name;
  speakerName.value = '';
}
const storage = shallowRef(null);
const configurationError = ref('');
if (credentials.accessKeyId && credentials.secretAccessKey) {
  try {
    storage.value = connectMinio(credentials);
  } catch (error) {
    configurationError.value = error.message;
  }
} else {
  configurationError.value = 'Tetapkan kelayakan MinIO dalam .env.local untuk menggunakan studio.';
}
const dialog = ref(null);
const player = ref(null);
const playback = ref('');
const playbackError = ref('');
const captionUrl = ref('');
const captionError = ref('');
const captions = ref([]);
const activeCue = ref(-1);
const cueList = ref(null);
let playbackRequest = 0;
let captionRequest = 0;
const uploadFile = ref(null);
const uploadField = ref(null);
const uploading = ref(false);
const uploadError = ref('');
const deletionFeedback = ref('');
const deletionError = ref(false);
const refreshing = ref(false);
let pollTimer;

function status(value) {
  return statusText[value] || value || 'Menyemak…';
}
const showReportProgress = job => ['STARTED', 'RUNNING', 'COMPLETED'].includes(job.report?.status);
const reportBusy = job => job.checkingReport || (job.reportId && !finished.has(job.report?.status));
const captionsPending = job => Boolean(job.pendingCues?.length || job.pendingCaptionUpdates?.length);
const hasTiming = row => Number.isFinite(row.start) && Number.isFinite(row.end);
const timingEdited = row => row.start !== row.savedStart || row.end !== row.savedEnd;
const dirty = job => job.review && (transcriptText(job.rows) !== job.savedText || captionsPending(job)
  || job.rows.some(row => row.captionBefore != null || timingEdited(row)));
const editingLocked = job => job.deleting || job.loading || job.saving || job.generating || reportBusy(job);
const jobPath = job => '/jobs/' + encodeURIComponent(job.id);
const reportPath = job => jobPath(job) + '/kpn-report';
const reportStatusPath = (job, id) => reportPath(job) + '/' + encodeURIComponent(id);

function persist() {
  try {
    const entries = jobs.value.map(({ id, name, bucket, key, pending, reportId, kind, duration, thumbnail, template }) =>
      ({ id, name, bucket, key, pending, reportId, kind, duration, thumbnail, template }));
    localStorage.setItem(queueKey, JSON.stringify(entries));
  } catch {
    // Keep the queue usable in memory when browser storage is unavailable.
  }
}

function recordingKind(job) {
  return job.kind || mediaKind({ name: job.name, type: '' });
}

function asrState(job) {
  if (job.pending || job.asr?.currentStep === 'WAITING'
      || ['CREATED', 'STARTED', 'QUEUED', 'WAITING'].includes(job.asr?.status)) return 'queue';
  if (job.asr?.status === 'PROCESSING' || job.asr?.status === 'RUNNING') return 'processing';
  if (job.asr?.status === 'COMPLETED') return 'completed';
  return 'other';
}

const asrProgress = job => Math.min(100, Math.max(0, Number(job.asr?.progressPercent) || 0));

function openUpload() {
  uploadError.value = '';
  dialog.value.showModal();
}

async function loadPlayback(job) {
  const request = ++playbackRequest;
  playback.value = '';
  playbackError.value = '';
  playing.value = false;
  if (!storage.value || !job) return;
  try {
    const url = await storage.value.playbackUrl(job.bucket, job.key);
    if (request === playbackRequest) playback.value = url;
  } catch (error) {
    if (request === playbackRequest) playbackError.value = error.message;
  }
}

function clearCaptions() {
  if (captionUrl.value) URL.revokeObjectURL(captionUrl.value);
  captionUrl.value = '';
  captions.value = [];
  activeCue.value = -1;
  captionError.value = '';
}

async function loadCaptions(job) {
  const request = ++captionRequest;
  clearCaptions();
  if (!storage.value || !job || (job.asr?.status !== 'COMPLETED' && !job.review)) return;
  try {
    const response = await storage.value('GET', job.bucket, `results/${job.id}/transcript.vtt`);
    const vtt = await response.text();
    if (request !== captionRequest) return;
    captions.value = parseVtt(vtt);
    captionUrl.value = URL.createObjectURL(new Blob([vtt], { type: 'text/vtt' }));
    updateCue();
  } catch (error) {
    if (request !== captionRequest) return;
    captionError.value = error.status === 404 ? 'Sari kata VTT belum tersedia.' : error.message;
  }
}

function updateCue() {
  const time = player.value?.currentTime;
  const next = captions.value.findLastIndex(cue => time >= cue.start && time < cue.end);
  if (activeCue.value === next) return;
  activeCue.value = next;
  if (next >= 0) nextTick(() => {
    const list = cueList.value;
    const row = list?.children[next];
    if (row && typeof list.scrollTo === 'function') {
      list.scrollTo({ top: Math.max(0, row.offsetTop - list.clientHeight / 3), behavior: 'smooth' });
    }
  });
}

function seek(cue) {
  if (!player.value) return;
  player.value.currentTime = cue.start;
  updatePlaybackTime();
}

function updateDuration(job) {
  const duration = player.value?.duration;
  if (Number.isFinite(duration) && job.duration !== duration) {
    job.duration = duration;
    persist();
  }
}

watch(selectedId, () => void loadPlayback(current.value));
watch(() => [current.value?.id, current.value?.asr?.status], () => void loadCaptions(current.value));

async function register(job) {
  job.registering = true;
  job.error = '';
  try {
    const response = await startTranscription(job);
    job.asr = response;
    job.pending = false;
    persist();
  } catch (error) {
    job.error = 'Rakaman sudah berada di MinIO. ' + error.message;
  } finally {
    job.registering = false;
  }
}

async function upload() {
  if (!storage.value || !uploadFile.value || uploading.value) return;
  uploading.value = true;
  uploadError.value = '';
  const file = uploadFile.value;
  try {
    const id = crypto.randomUUID();
    const filename = file.name.replace(/[\\/]/g, '_');
    const metadata = inspectMedia(file);
    const key = `uploads/${id}/${filename}`;
    await storage.value('PUT', settings.bucket, key, {
      body: file, headers: { 'content-type': file.type || 'application/octet-stream' },
    });
    jobs.value.unshift({ id, name: filename, bucket: settings.bucket, key, pending: true, ...await metadata });
    const job = jobs.value[0];
    persist();
    dialog.value.close();
    uploadFile.value = null;
    uploadField.value.value = '';
    await register(job);
  } catch (error) {
    uploadError.value = error.message;
  } finally {
    uploading.value = false;
  }
  void refresh();
}

async function openTranscript(job, force = false) {
  if (!storage.value || job.loading || (job.review && !force)) return;
  if (force && dirty(job) && !window.confirm('Buang suntingan yang belum disimpan?')) return;
  job.loading = true;
  job.error = '';
  try {
    job.review = await readTranscript(storage.value, job);
    job.rows = transcriptRows(job.review.document.text);
    assignRowTimes(job.rows, job.review.document.segments, job.review.document.studioRowTimings);
    job.savedText = transcriptText(job.rows);
    sortTranscriptRows(job.rows);
    job.pendingCues = job.review.document.studioTimedCues || [];
    job.pendingCaptionUpdates = job.review.document.studioCaptionUpdates || [];
    for (const row of job.rows) {
      row.savedLine = transcriptText([row]);
      row.savedStart = row.start;
      row.savedEnd = row.end;
      const original = job.pendingCues.find(cue => cue.start === row.start && cue.end === row.end
        && transcriptText([cue]) === row.savedLine);
      if (!row.cueId && original) row.cueId = original.id;
    }
    if (captionsPending(job) || Array.isArray(job.review.document.studioRowTimings)) {
      const captionFile = await readCaptionDocument(storage.value, job);
      if (editVttCues(captionFile.text, job.pendingCues, job.pendingCaptionUpdates) === captionFile.text) {
        job.pendingCues = [];
        job.pendingCaptionUpdates = [];
        const cues = parseVtt(captionFile.text);
        for (const row of job.rows) {
          const matches = cues.filter(cue => Math.abs(cue.start - row.start) <= 0.002
            && Math.abs(cue.end - row.end) <= 0.002);
          if (matches.length === 1 && matches[0].text.replace(/\s+/g, ' ').trim() !== row.savedLine) {
            row.captionBefore = matches[0].text;
          }
        }
      }
    }
    if (selectedId.value === job.id) {
      selectedSpeaker.value = '';
      speakerName.value = '';
      resetTimedEntry();
    }
  } catch (error) {
    job.error = error.message;
  } finally {
    job.loading = false;
  }
}

async function refreshJob(job) {
  if (job.pending || job.deleting) return;
  try {
    if (!finished.has(job.asr?.status)) job.asr = await api(jobPath(job));
    if (job.id === selectedId.value && job.asr?.status === 'COMPLETED' && !job.review && !job.error) {
      await openTranscript(job);
    }
    if (job.reportId && !job.generating && !finished.has(job.report?.status)) {
      const marker = job.report;
      try {
        const next = await api(reportStatusPath(job, job.reportId));
        if (marker === job.report && !job.generating) job.report = next;
      } catch (error) {
        if (error.status !== 404) throw error;
        if (marker === job.report && !job.generating) {
          job.reportId = null;
          job.report = null;
          persist();
        }
      }
    }
    job.pollError = '';
  } catch (error) {
    job.pollError = error.message;
  }
}

async function refresh() {
  if (refreshing.value) return;
  refreshing.value = true;
  try {
    await Promise.all(jobs.value.map(refreshJob));
  } finally {
    refreshing.value = false;
  }
}

function recordingLimit(job) {
  const playerDuration = player.value?.duration;
  const duration = Number.isFinite(playerDuration) && playerDuration > 0 ? playerDuration : job.duration;
  return Number.isFinite(duration) && duration > 0 ? Math.floor(duration * 1000) / 1000 : null;
}

function setCueTime(field, time) {
  if (!Number.isFinite(time) || !current.value) return;
  const limit = recordingLimit(current.value) ?? Infinity;
  const span = Math.max(captionGap, timedEntry.end - timedEntry.start);
  const rounded = Math.round(time * 1000) / 1000;
  if (field === 'start') {
    timedEntry.start = Math.max(0, Math.min(rounded, limit - captionGap));
    if (timedEntry.end <= timedEntry.start) {
      timedEntry.end = Math.min(limit, Math.round((timedEntry.start + span) * 1000) / 1000);
    }
  } else {
    timedEntry.end = Math.max(captionGap, Math.min(rounded, limit));
    if (timedEntry.start >= timedEntry.end) {
      timedEntry.start = Math.max(0, Math.round((timedEntry.end - span) * 1000) / 1000);
    }
  }
  timedEntryError.value = '';
}

function currentPlayerTime() {
  const time = player.value?.currentTime;
  if (!current.value || !Number.isFinite(time)) return null;
  const limit = recordingLimit(current.value) ?? Infinity;
  return Math.max(0, Math.min(limit, Math.round(time * 1000) / 1000));
}

function followPlayerTime() {
  const time = currentPlayerTime();
  if (!captureField.value || time === null) return;
  if (captureField.value === 'start') {
    timedEntry.start = time;
  } else if (time > timedEntry.start) {
    timedEntry.end = time;
    timedEntryError.value = '';
  }
}

function updatePlaybackTime() {
  updateCue();
  followPlayerTime();
}

function startTimeCapture() {
  if (!timedEntryOpen.value || currentPlayerTime() === null) return;
  captureBefore = { start: timedEntry.start, end: timedEntry.end };
  captureField.value = 'start';
  timedEntryError.value = '';
  followPlayerTime();
}

function setCapturedTime() {
  const time = currentPlayerTime();
  if (time === null || !current.value) return;
  if (captureField.value === 'start') {
    const limit = recordingLimit(current.value);
    if (limit !== null && time >= limit) {
      timedEntryError.value = 'Alihkan pemain ke sebelum penghujung rakaman untuk menetapkan masa mula.';
      return;
    }
    timedEntry.start = time;
    timedEntry.end = Math.min(limit ?? Infinity, Math.round((time + captionGap) * 1000) / 1000);
    captureField.value = 'end';
  } else if (captureField.value === 'end') {
    if (time <= timedEntry.start) {
      timedEntryError.value = 'Gerakkan pemain melepasi masa mula untuk menetapkan masa tamat.';
      return;
    }
    timedEntry.end = time;
    captureField.value = null;
    captureBefore = null;
  }
  timedEntryError.value = '';
}

function cancelTimeCapture() {
  if (captureBefore) Object.assign(timedEntry, captureBefore);
  captureBefore = null;
  captureField.value = null;
  timedEntryError.value = '';
}

async function togglePlayback() {
  if (!player.value) return;
  if (playing.value) {
    player.value.pause();
  } else {
    try {
      await player.value.play();
    } catch {
      playbackError.value = 'Rakaman tidak dapat dimainkan.';
    }
  }
}

function openTimedEntry(job, row = null) {
  resetTimedEntry();
  timingRow.value = row;
  if (row) {
    timedEntry.start = row.start;
    timedEntry.end = row.end;
    timedEntryOpen.value = true;
    void nextTick(() => timingForm.value?.scrollIntoView?.({ block: 'nearest' }));
    return;
  }
  const limit = recordingLimit(job);
  const playhead = player.value?.currentTime || 0;
  timedEntry.start = Math.max(0, Math.min(Math.round(playhead * 1000) / 1000, (limit ?? Infinity) - captionGap));
  timedEntry.end = Math.min(limit ?? Infinity, Math.round((timedEntry.start + 3) * 1000) / 1000);
  timedEntryOpen.value = true;
}

function applyTimedEntry(job) {
  if (editingLocked(job) || captureField.value) return;
  const { start, end } = timedEntry;
  if (!Number.isSafeInteger(Math.round(start * 1000)) || !Number.isSafeInteger(Math.round(end * 1000))
      || start < 0 || end <= start) {
    timedEntryError.value = 'Masukkan masa mula dan tamat yang sah. Masa tamat mesti selepas masa mula.';
    return;
  }
  const limit = recordingLimit(job);
  if (limit !== null && end > limit) {
    timedEntryError.value = `Masa tamat ${formatVttTime(end)} melebihi tempoh rakaman ${formatVttTime(limit)}.`;
    return;
  }
  if (timingRow.value) {
    Object.assign(timingRow.value, { start, end });
  } else {
    const text = timedEntry.text.trim();
    if (!text) return;
    const cueId = 'studio-' + crypto.randomUUID();
    job.rows.push({ id: cueId, cueId, speaker: cleanSpeaker(timedEntry.speaker), text, start, end, newCue: true });
  }
  sortTranscriptRows(job.rows);
  resetTimedEntry();
}

function select(job) {
  selectedId.value = job.id;
  if (job.asr?.status === 'COMPLETED') void openTranscript(job);
  void refresh();
}

async function checkReport(job) {
  const id = job.reportId || 'kpn-output-' + job.id;
  try {
    job.report = await api(reportStatusPath(job, id));
    job.reportId = id;
    persist();
  } catch (error) {
    if (error.status !== 404) throw error;
  }
}

function canDelete(job) {
  return Boolean(storage.value && finished.has(job.asr?.status) && !editingLocked(job) && !job.downloading);
}

async function removeRecording(job) {
  if (!canDelete(job) || !window.confirm(`Padam "${job.name}" serta transkrip, sari kata dan laporan KPN daripada MinIO?`)) return;
  job.deleting = true;
  deletionFeedback.value = '';
  try {
    job.asr = await api(jobPath(job));
    if (!finished.has(job.asr.status)) throw new Error('Transkripsi masih dalam giliran atau sedang diproses.');
    await checkReport(job);
    if (reportBusy(job)) throw new Error('Tunggu laporan KPN selesai sebelum memadam rakaman.');
    await deleteRecording(storage.value, job);
    if (selectedId.value === job.id) selectedId.value = null;
    jobs.value = jobs.value.filter(entry => entry.id !== job.id);
    persist();
    deletionError.value = false;
    deletionFeedback.value = 'Rakaman dan fail MinIO telah dipadam.';
  } catch (error) {
    deletionError.value = true;
    deletionFeedback.value = 'Pemadaman tidak selesai. ' + error.message;
  } finally {
    job.deleting = false;
  }
}

async function save(job) {
  if (!storage.value || !job.review || !dirty(job) || editingLocked(job)) return;
  const text = transcriptText(job.rows);
  const newRows = job.rows.filter(row => row.newCue && row.text.trim());
  const additions = newRows.map(row => ({
    id: row.id, speaker: cleanSpeaker(row.speaker), text: row.text.replace(/\s*\r?\n\s*/g, ' ').trim(),
    start: row.start, end: row.end,
  }));
  if (!text.trim()) return;
  const updates = job.rows.flatMap(row => {
    const line = transcriptText([row]);
    const retimed = timingEdited(row);
    if (row.newCue || !hasTiming(row) || (line === row.savedLine && row.captionBefore == null && !retimed)) return [];
    row.cueId ||= 'studio-edit-' + crypto.randomUUID();
    return [{
      id: row.cueId, start: row.start, end: row.end, before: row.captionBefore ?? row.savedLine,
      beforeStart: row.savedStart, beforeEnd: row.savedEnd, retimed, line,
    }];
  });
  job.saving = true;
  job.error = '';
  try {
    await checkReport(job);
    if (reportBusy(job)) throw new Error('Tunggu laporan semasa selesai sebelum menyimpan suntingan.');
    if (text !== job.savedText || additions.length || updates.length) {
      job.review = await saveTranscript(storage.value, job, job.review, text, additions, rowTimings(job.rows), updates);
      job.savedText = text;
      job.pendingCues = job.review.document.studioTimedCues || [];
      job.pendingCaptionUpdates = job.review.document.studioCaptionUpdates || [];
      for (const row of newRows) row.newCue = false;
      for (const row of job.rows) {
        row.savedLine = transcriptText([row]);
        row.savedStart = row.start;
        row.savedEnd = row.end;
        row.captionBefore = null;
      }
    }
    if (captionsPending(job)) {
      try {
        await saveTimedCaptions(storage.value, job, job.pendingCues, job.pendingCaptionUpdates);
        job.pendingCues = [];
        job.pendingCaptionUpdates = [];
        if (selectedId.value === job.id) void loadCaptions(job);
      } catch (error) {
        throw new Error('Transkrip disimpan. Sari kata belum disimpan. ' + error.message);
      }
    }
  } catch (error) {
    job.error = error.message;
  } finally {
    job.saving = false;
  }
}

function chooseTemplate(job, files) {
  if (!storage.value || editingLocked(job) || !files?.length) return;
  try {
    if (files.length !== 1) throw new Error('Pilih satu fail templat Word sahaja.');
    validateReportTemplate(files[0]);
    job.templateFile = files[0];
    job.error = '';
  } catch (error) {
    job.error = error.message;
  }
}

function dragTemplate(job, event) {
  const transfer = event.dataTransfer;
  if (!transfer?.types.includes('Files')) return;
  templateDragging.value = Boolean(storage.value && !editingLocked(job));
  transfer.dropEffect = templateDragging.value ? 'copy' : 'none';
}

function leaveTemplate(event) {
  if (!event.relatedTarget || !event.currentTarget.contains(event.relatedTarget)) {
    templateDragging.value = false;
  }
}

function dropTemplate(job, event) {
  templateDragging.value = false;
  chooseTemplate(job, event.dataTransfer?.files);
}

function clearTemplate(job) {
  job.template = null;
  job.templateFile = null;
  persist();
}

async function generate(job) {
  if (!storage.value || !job.review || dirty(job) || editingLocked(job)) return;
  job.generating = true;
  job.error = '';
  try {
    if (job.templateFile) {
      job.template = await uploadReportTemplate(storage.value, job, job.templateFile);
      job.templateFile = null;
      persist();
    }
    const query = job.template
      ? '?' + new URLSearchParams({ templateId: job.template.id, templateName: job.template.name }) : '';
    job.report = await api(reportPath(job) + query, { method: 'POST' });
    job.reportId = job.report.workflowId;
    persist();
    if (job.template && job.report.templateId !== job.template.id) {
      job.error = 'API tidak mengesahkan templat yang dipilih. Pastikan asr-api dan kpn-output menggunakan versi terkini.';
    }
  } catch (error) {
    if (error.data?.code === 'REPORT_ALREADY_RUNNING' && error.data.workflowId) {
      job.reportId = error.data.workflowId;
      job.report = { status: 'RUNNING' };
      persist();
    } else {
      job.error = error.message;
    }
  } finally {
    job.generating = false;
    void refresh();
  }
}

const defaultTemplateName = 'Rapporteur_Kertas_01.docx';

function reportMatchesTemplate(job) {
  if (job.templateFile || job.generating || job.report?.status !== 'COMPLETED' || !job.report.outputKey) return false;
  const expected = job.template?.name || defaultTemplateName;
  return (job.report.templateId || null) === (job.template?.id || null)
    && job.report.outputKey.split('/').at(-1) === expected;
}

function reportStatusLabel(job) {
  if (job.generating) return 'Menghantar ke giliran…';
  if (job.templateFile || (job.report?.status === 'COMPLETED' && !reportMatchesTemplate(job))) {
    return 'Perlu dijana';
  }
  if (job.report?.currentStep === 'QUEUED') return 'Dalam giliran';
  return job.reportId ? status(job.report?.status) : 'Belum dijana';
}

function reportNotice(job) {
  if (job.templateFile) return 'Jana laporan untuk menggunakan templat baharu.';
  if (job.report?.status === 'COMPLETED' && !reportMatchesTemplate(job)) {
    return 'Jana laporan untuk menyediakan fail Word bagi templat yang dipilih.';
  }
  return '';
}

async function exportWord(job) {
  if (!storage.value || !reportMatchesTemplate(job) || job.downloading) return;
  job.downloading = true;
  job.error = '';
  try {
    await downloadReport(storage.value, job, job.report);
  } catch (error) {
    job.error = error.message;
  } finally {
    job.downloading = false;
  }
}

function warnUnsaved(event) {
  if (jobs.value.some(dirty)) {
    event.preventDefault();
    event.returnValue = '';
  }
}

onMounted(() => {
  void refresh();
  pollTimer = setInterval(refresh, 4000);
  window.addEventListener('beforeunload', warnUnsaved);
});
onBeforeUnmount(() => {
  clearInterval(pollTimer);
  window.removeEventListener('beforeunload', warnUnsaved);
  captionRequest++;
  playbackRequest++;
  clearCaptions();
});
</script>

<template>
  <header class="topbar">
    <div class="shell bar-content">
      <div class="brand">
        <img class="brand-mark" src="/2en-apps-logo.png" alt="">
        2en Apps - V2T
      </div>
    </div>
  </header>

  <main class="shell">
    <div class="page-title">
      <div><h1>Rakaman &amp; laporan</h1><p>{{ jobs.length }} rakaman</p></div>
      <div class="actions">
        <button class="primary" type="button" :disabled="!storage" @click="openUpload">+ Tambah</button>
      </div>
    </div>
    <p v-if="configurationError" class="error setup-error" role="alert">{{ configurationError }}</p>

    <section class="panel table-panel" aria-label="Senarai rakaman">
      <div class="table-scroll">
        <table>
          <thead><tr><th scope="col">Rakaman</th><th scope="col">Tempoh</th><th scope="col">Transkripsi</th><th scope="col">Laporan KPN</th><th scope="col" class="table-actions">Tindakan</th></tr></thead>
          <tbody>
            <tr v-if="!jobs.length">
              <td colspan="5" class="empty-row">
                <span class="empty-mark" aria-hidden="true">♪</span>
                <strong>Belum ada rakaman</strong>
                <p>Pilih Tambah untuk memuat naik audio atau video.</p>
              </td>
            </tr>
            <tr v-for="job in jobs" :key="job.id" :class="{ selected: job.id === selectedId }">
              <th scope="row">
                <button class="recording" type="button" :aria-pressed="job.id === selectedId" @click="select(job)">
                  <img v-if="job.thumbnail" class="thumbnail" :src="job.thumbnail" alt="">
                  <span v-else class="thumbnail placeholder" aria-hidden="true">{{ recordingKind(job) === 'video' ? '▶' : '♪' }}</span>
                  <span><strong>{{ job.name }}</strong><small>{{ recordingKind(job) === 'video' ? 'Video' : 'Audio' }}</small></span>
                </button>
              </th>
              <td>{{ formatTime(job.duration) }}</td>
              <td>
                <span v-if="asrState(job) === 'queue'" class="tag queued">Dalam giliran</span>
                <span v-else-if="asrState(job) === 'completed'" class="tag">Selesai</span>
                <div v-else-if="asrState(job) === 'processing'" class="progress">
                  <progress :value="asrProgress(job)" max="100" :aria-label="'Kemajuan transkripsi ' + job.name"></progress>
                  <span>{{ asrProgress(job) }}%</span>
                </div>
                <span v-else class="muted">{{ status(job.asr?.status) }}</span>
              </td>
              <td class="muted">
                <ReportProgress v-if="showReportProgress(job)" :report="job.report" compact />
                <span v-else-if="job.reportId" class="tag report-tag"
                      :class="{ queued: ['CREATED', 'STARTED', 'RUNNING'].includes(job.report?.status) }">
                  {{ job.report?.currentStep === 'QUEUED' ? 'Dalam giliran' : status(job.report?.status) }}
                </span>
                <span v-else aria-label="Tiada laporan">—</span>
              </td>
              <td class="table-actions">
                <button class="danger-outline" type="button" :disabled="!canDelete(job)"
                        :aria-label="'Padam ' + job.name" @click="removeRecording(job)">
                  {{ job.deleting ? 'Memadam…' : 'Padam' }}
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
    <p v-if="deletionFeedback" class="table-feedback" :class="deletionError ? 'error' : 'muted'" role="status">
      {{ deletionFeedback }}
    </p>

    <dialog ref="dialog" @cancel.prevent="!uploading && dialog.close()">
      <form class="upload" @submit.prevent="upload">
        <h2>Tambah rakaman</h2>
        <label for="media">Fail audio atau video</label>
        <input id="media" ref="uploadField" type="file" accept="audio/*,video/*,.mkv,.ogg,.m4a" required
               @change="uploadFile = $event.target.files[0]">
        <p v-if="uploadError" class="error" role="alert">{{ uploadError }}</p>
        <div class="actions">
          <button class="outline" type="button" :disabled="uploading" @click="dialog.close()">Batal</button>
          <button class="primary" type="submit" :disabled="!uploadFile || uploading">
            {{ uploading ? 'Sedang memuat naik…' : 'Muat naik ke MinIO' }}
          </button>
        </div>
      </form>
    </dialog>

    <section v-if="current" class="panel details" aria-labelledby="recording-title">
      <div class="editor-heading">
        <div>
          <h2 id="recording-title">{{ current.name }}</h2>
          <p class="muted">{{ recordingKind(current) === 'video' ? 'Video' : 'Audio' }} · {{ formatTime(current.duration) }}</p>
        </div>
        <button class="icon-action" type="button" aria-label="Tutup" title="Tutup" @click="selectedId = null">
          <span class="close-icon" aria-hidden="true">×</span>
        </button>
      </div>
      <div class="playback-grid">
        <div class="media-panel">
          <component :is="recordingKind(current)" v-if="playback" ref="player" :key="playback"
                     :src="playback" controls playsinline preload="metadata"
                     :poster="recordingKind(current) === 'video' ? current.thumbnail : undefined"
                     @loadedmetadata="updateDuration(current)" @timeupdate="updatePlaybackTime" @seeked="updatePlaybackTime"
                     @play="playing = true" @pause="playing = false" @ended="playing = false"
                     @error="playbackError = 'Rakaman tidak dapat dimainkan. Semak akses MinIO dan format media.'">
            <track v-if="captionUrl" :src="captionUrl" kind="captions" srclang="ms" label="Transkrip" default>
          </component>
          <div v-if="playbackError">
            <p class="error" role="alert">{{ playbackError }}</p>
            <button class="outline retry-player" type="button" @click="loadPlayback(current)">Muat semula pemain</button>
          </div>
        </div>
        <div class="caption-panel">
          <h3>Transkrip segerak</h3>
          <p v-if="captionError" class="muted">{{ captionError }}</p>
          <p v-else-if="!captions.length" class="muted">Sari kata tersedia selepas transkripsi selesai.</p>
          <ol v-else ref="cueList" class="caption-list" aria-label="Sari kata mengikut masa">
            <li v-for="(cue, index) in captions" :key="index">
              <button type="button" :aria-current="index === activeCue ? 'true' : undefined" @click="seek(cue)">
                <time>{{ formatTime(cue.start) }}</time>
                <span><strong v-if="splitSpeaker(cue.text).speaker" class="cue-speaker">{{ splitSpeaker(cue.text).speaker }}</strong>{{ splitSpeaker(cue.text).text }}</span>
              </button>
            </li>
          </ol>
        </div>
      </div>

      <div class="review">
        <h3>Semakan transkrip untuk laporan</h3>
        <div v-if="!current.review" class="empty">
          <p v-if="current.loading">Memuatkan transkrip…</p>
          <p v-else-if="current.pending">Rakaman tersedia di MinIO dan belum dihantar untuk transkripsi.</p>
          <p v-else>Transkrip akan dipaparkan apabila tersedia.</p>
          <button v-if="current.pending" class="outline" type="button" :disabled="current.registering" @click="register(current)">Hantar ke giliran transkripsi</button>
          <button v-else class="outline" type="button" :disabled="!storage || current.loading" @click="openTranscript(current)">Buka transkrip</button>
        </div>
        <template v-else>
          <form class="speaker-tools" @submit.prevent="applySpeakerName">
            <label>Penutur
              <select v-model="selectedSpeaker" :disabled="editingLocked(current) || !speakers.length">
                <option value="">Pilih penutur</option>
                <option v-for="speaker in speakers" :key="speaker">{{ speaker }}</option>
              </select>
            </label>
            <label>Nama baharu
              <input v-model="speakerName" maxlength="80" :disabled="editingLocked(current) || !selectedSpeaker">
            </label>
            <button class="outline" type="submit"
                    :disabled="editingLocked(current) || !selectedSpeaker || !cleanSpeaker(speakerName) || cleanSpeaker(speakerName) === selectedSpeaker">
              Tukar semua nama
            </button>
            <button v-if="!timedEntryOpen" class="outline timed-toggle" type="button"
                    :disabled="editingLocked(current)" @click="openTimedEntry(current)">+ Tambah teks dan masa</button>
          </form>
          <form v-if="timedEntryOpen" ref="timingForm" class="timed-entry" @submit.prevent="applyTimedEntry(current)">
            <h4 v-if="timingRow">Ubah masa sari kata</h4>
            <div v-else class="timed-fields">
              <label>Penutur <input v-model="timedEntry.speaker" maxlength="80" :disabled="editingLocked(current)"></label>
            </div>
            <div v-if="player" class="capture-controls">
              <button v-if="!captureField" class="outline" type="button" :disabled="editingLocked(current)"
                      @click="startTimeCapture">Ikut masa pemain</button>
              <template v-else>
                <p class="muted" role="status">
                  {{ captureField === 'start' ? 'Masa mula mengikuti pemain.' : 'Masa mula dikunci. Masa tamat mengikuti pemain.' }}
                </p>
                <div class="actions">
                  <button class="outline" type="button" @click="togglePlayback">{{ playing ? 'Jeda' : 'Main' }}</button>
                  <button class="primary" type="button" @click="setCapturedTime">
                    {{ captureField === 'start' ? 'Tetapkan masa mula' : 'Tetapkan masa tamat' }}
                  </button>
                  <button class="quiet" type="button" @click="cancelTimeCapture">Batal ikut pemain</button>
                </div>
              </template>
            </div>
            <div class="time-pickers">
              <div v-for="option in timeFields" :key="option.field" class="caption-time-field" :class="{ tracking: captureField === option.field }">
                <div class="time-picker-heading">
                  <label :for="'cue-' + option.field">{{ option.label }}</label>
                  <output :for="'cue-' + option.field">{{ formatVttTime(timedEntry[option.field]) }}</output>
                </div>
                <input :id="'cue-' + option.field" class="timeline-slider" type="range" min="0" step="0.001"
                       :max="recordingLimit(current) ?? Math.max(3, timedEntry.end)"
                       :value="timedEntry[option.field]" :aria-valuetext="formatVttTime(timedEntry[option.field])"
                       :disabled="editingLocked(current) || !!captureField || recordingLimit(current) === null"
                       @input="setCueTime(option.field, $event.target.valueAsNumber)">
                <div class="time-picker-actions">
                  <button class="outline" type="button" :disabled="editingLocked(current) || !!captureField"
                          :aria-label="'Undur ' + option.label.toLowerCase() + ' satu saat'"
                          @click="setCueTime(option.field, timedEntry[option.field] - 1)">−1s</button>
                  <button class="outline" type="button" :disabled="editingLocked(current) || !!captureField"
                          :aria-label="'Anjak ' + option.label.toLowerCase() + ' satu saat'"
                          @click="setCueTime(option.field, timedEntry[option.field] + 1)">+1s</button>
                  <button v-if="player" class="quiet" type="button" :disabled="editingLocked(current) || !!captureField"
                          @click="setCueTime(option.field, player.currentTime)">Guna masa pemain</button>
                </div>
              </div>
            </div>
            <p v-if="recordingLimit(current) !== null" class="muted">Tempoh rakaman {{ formatVttTime(recordingLimit(current)) }}</p>
            <label v-if="!timingRow">Teks baharu <textarea v-model="timedEntry.text" rows="2" required :readonly="editingLocked(current)"></textarea></label>
            <p v-if="timedEntryError" class="error" role="alert">{{ timedEntryError }}</p>
            <div class="actions">
              <button class="quiet" type="button" @click="resetTimedEntry">Batal</button>
              <button class="outline" type="submit" :disabled="editingLocked(current) || !!captureField">{{ timingRow ? 'Guna masa ini' : 'Tambah ke transkrip' }}</button>
            </div>
          </form>
          <div class="transcript-table">
            <table aria-label="Suntingan transkrip">
              <thead><tr><th scope="col">Penutur</th><th scope="col">Teks</th><th scope="col">Masa</th></tr></thead>
              <tbody>
                <tr v-for="(row, index) in current.rows" :key="row.id">
                  <td><input v-model="row.speaker" maxlength="80" :aria-label="'Penutur baris ' + (index + 1)"
                             :readonly="editingLocked(current)"></td>
                  <td><textarea v-model="row.text" spellcheck="false" rows="2" :aria-label="'Teks baris ' + (index + 1)"
                                :readonly="editingLocked(current)"></textarea></td>
                  <td class="cue-time-cell">
                    <div v-if="hasTiming(row)" class="cue-time-controls">
                      <small class="cue-time">{{ formatVttTime(row.start) }} – {{ formatVttTime(row.end) }}</small>
                      <button class="icon-action" type="button" :disabled="editingLocked(current)" title="Ubah masa"
                              :aria-label="'Ubah masa baris ' + (index + 1)" @click="openTimedEntry(current, row)">
                        <span class="clock-icon" aria-hidden="true"></span>
                      </button>
                    </div>
                    <span v-else class="muted">—</span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <div class="edit-actions">
            <span class="muted" role="status">{{ current.saving ? 'Menyimpan…' : captionsPending(current) ? 'Sari kata belum disimpan' : dirty(current) ? 'Suntingan belum disimpan' : '' }}</span>
            <div class="actions">
              <button class="quiet" type="button" :disabled="!storage || current.loading || current.saving" @click="openTranscript(current, true)">Muat semula teks</button>
              <button class="outline" type="button" :disabled="!storage || !dirty(current) || !hasTranscriptText || current.saving || current.generating || reportBusy(current)"
                      @click="save(current)">Simpan suntingan</button>
            </div>
          </div>
        </template>
      </div>
      <section v-if="current.review || current.reportId" class="export" aria-labelledby="kpn-report-title">
        <div class="report-header">
          <h3 id="kpn-report-title">Laporan KPN</h3>
          <span class="report-state" :class="{
            ready: reportMatchesTemplate(current),
            busy: current.generating || reportBusy(current),
            failed: current.report?.status === 'FAILED',
          }" role="status">
            <span aria-hidden="true"></span>{{ reportStatusLabel(current) }}
          </span>
        </div>
        <ReportProgress v-if="showReportProgress(current)" :report="current.report" />
        <div v-if="current.review" class="report-template" :class="{ 'drop-active': templateDragging }"
             @dragover.prevent="dragTemplate(current, $event)" @dragleave="leaveTemplate"
             @drop.prevent="dropTemplate(current, $event)">
          <span class="word-icon" aria-hidden="true">W</span>
          <div class="template-details">
            <span class="muted">Templat Word · Seret fail .docx ke sini</span>
            <strong>{{ current.templateFile?.name || current.template?.name || defaultTemplateName }}</strong>
          </div>
          <div class="template-actions">
            <input :key="current.id + (current.templateFile?.name || current.template?.id || '')"
                   :id="'report-template-' + current.id" class="sr-only template-input" type="file" accept=".docx"
                   :disabled="!storage || editingLocked(current)" @change="chooseTemplate(current, $event.target.files); $event.target.value = ''">
            <label class="template-picker" :for="'report-template-' + current.id">Pilih fail Word</label>
            <button v-if="current.templateFile || current.template" class="quiet" type="button"
                    :disabled="editingLocked(current)" @click="clearTemplate(current)">Guna templat asal</button>
          </div>
        </div>
        <div class="report-footer">
          <p v-if="reportNotice(current)" class="muted">{{ reportNotice(current) }}</p>
          <div class="actions">
            <button :class="reportMatchesTemplate(current) ? 'outline' : 'primary'" type="button"
                    :disabled="!storage || !current.review || dirty(current) || editingLocked(current)"
                    @click="generate(current)">Jana laporan</button>
            <button v-if="reportMatchesTemplate(current)" class="primary" type="button"
                    :disabled="!storage || current.downloading" @click="exportWord(current)">
              {{ current.downloading ? 'Memuat turun…' : 'Muat turun Word' }}
            </button>
          </div>
        </div>
      </section>
      <p v-if="current.error || current.pollError || current.report?.errorMessage || current.asr?.errorMessage" class="error" role="alert">
        {{ current.error || current.pollError || current.report?.errorMessage || current.asr?.errorMessage }}
      </p>
    </section>
  </main>
</template>
