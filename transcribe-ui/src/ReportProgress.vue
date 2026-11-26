<script setup>
import { computed } from 'vue';

const props = defineProps({
  report: { type: Object, required: true },
  compact: Boolean,
});

const stageLabels = {
  QUEUED: 'Dalam giliran',
  DONE: 'Selesai',
  CHUNKING: 'Menyediakan transkrip',
  EXTRACTING_EVIDENCE: 'Mencari isi daripada transkrip',
  WRITING_JSON: 'Menyusun laporan',
  RENDERING_DOCX: 'Menjana fail Word',
};

const stage = computed(() => {
  if (props.report.status === 'COMPLETED') return stageLabels.DONE;
  if (props.report.status === 'STARTED') return stageLabels.QUEUED;
  const step = props.report.currentStep;
  const section = /^SECTION_([1-8])$/.exec(step || '');
  return section ? `Menulis bahagian ${section[1]} daripada 8` : stageLabels[step] || 'Sedang dijana';
});
const percent = computed(() => {
  if (props.report.status === 'COMPLETED') return 100;
  if (props.report.status === 'STARTED' || props.report.currentStep === 'QUEUED') return 0;
  return Number.isFinite(props.report.progressPercent)
    ? Math.max(0, Math.min(100, props.report.progressPercent)) : null;
});
</script>

<template>
  <div class="report-progress" :class="{ compact }">
    <div class="report-progress-label"><span>{{ stage }}</span><strong class="report-percent" v-if="percent !== null">{{ percent }}%</strong></div>
    <progress v-if="percent !== null" :value="percent" max="100" :aria-label="stage"></progress>
    <progress v-else :aria-label="stage"></progress>
  </div>
</template>
