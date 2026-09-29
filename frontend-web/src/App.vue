<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";
import { RouterLink, RouterView } from "vue-router";
import AuthView from "./views/AuthView.vue";
import { ApiError, apiFetch } from "./api/client";
import { getAccessToken, getCurrentUser, logout, type AuthUser } from "./api/auth";

type AppStatus = "loading" | "ready" | "auth" | "unavailable";
const status = ref<AppStatus>("loading");
const currentUser = ref<AuthUser | null>(null);
const connectionError = ref("");

async function initialize() {
  status.value = "loading";
  connectionError.value = "";
  try {
    const capabilities = await apiFetch<{ auth_enabled: boolean }>("/api/v1/system/capabilities");
    if (!capabilities.auth_enabled) {
      status.value = "ready";
      return;
    }
    if (!getAccessToken()) {
      status.value = "auth";
      return;
    }
    currentUser.value = await getCurrentUser();
    status.value = "ready";
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      logout();
      currentUser.value = null;
      status.value = "auth";
      return;
    }
    connectionError.value = error instanceof Error ? error.message : "无法连接后端服务";
    status.value = "unavailable";
  }
}

function handleSessionExpired() {
  logout();
  currentUser.value = null;
  status.value = "auth";
}

function reloadSession() {
  window.location.assign("/");
}

function signOut() {
  logout();
  reloadSession();
}

onMounted(() => {
  window.addEventListener("jobmatch-auth-expired", handleSessionExpired);
  void initialize();
});
onUnmounted(() => window.removeEventListener("jobmatch-auth-expired", handleSessionExpired));
</script>

<template>
  <div v-if="status === 'loading'" class="auth-page auth-status" role="status">
    <span class="auth-brand">CS JobMate</span>
    <p>正在连接工作区...</p>
  </div>
  <div v-else-if="status === 'unavailable'" class="auth-page auth-status">
    <span class="auth-brand">CS JobMate</span>
    <p class="error-message">{{ connectionError }}</p>
    <button type="button" @click="initialize">重试连接</button>
  </div>
  <AuthView v-else-if="status === 'auth'" @authenticated="reloadSession" />
  <div v-else class="app-shell">
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
      <div v-if="currentUser" class="account-controls">
        <span class="account-email" :title="currentUser.email">{{ currentUser.email }}</span>
        <button type="button" class="account-logout" @click="signOut">退出登录</button>
      </div>
      <p class="side-note">AI 给出证据和建议，最终决定始终由你完成。</p>
    </aside>
    <main class="main-content"><RouterView /></main>
  </div>
</template>
