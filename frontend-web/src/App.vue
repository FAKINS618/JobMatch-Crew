<script setup lang="ts">
import { RouterLink, RouterView } from "vue-router";
</script>

<template>
  <div class="app-shell">
    <aside class="side-nav">
      <RouterLink class="brand" to="/">CS JobMate</RouterLink>
      <nav>
        <RouterLink to="/">今日工作台</RouterLink>
        <RouterLink to="/copilot">AI 副驾</RouterLink>
        <RouterLink to="/resumes">简历中心</RouterLink>
        <RouterLink to="/actions">成长计划</RouterLink>
        <RouterLink to="/pipeline">投递管道</RouterLink>
        <RouterLink to="/inbox">岗位收件箱</RouterLink>
      </nav>
      <p class="side-note">AI 给出证据和建议，最终决定始终由你完成。</p>
    </aside>
    <main class="main-content"><RouterView /></main>
  </div>
</template>
