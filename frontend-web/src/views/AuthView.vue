<script setup lang="ts">
import { ref } from "vue";
import { login, register } from "@/api/auth";

const emit = defineEmits<{ authenticated: [] }>();
const mode = ref<"login" | "register">("login");
const email = ref("");
const password = ref("");
const isSubmitting = ref(false);
const errorMessage = ref("");

function changeMode(next: "login" | "register") {
  mode.value = next;
  errorMessage.value = "";
}

async function submit() {
  if (isSubmitting.value) return;
  isSubmitting.value = true;
  errorMessage.value = "";
  try {
    if (mode.value === "login") {
      await login(email.value, password.value);
    } else {
      await register(email.value, password.value);
    }
    emit("authenticated");
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "账号验证失败，请重试";
  } finally {
    isSubmitting.value = false;
  }
}
</script>

<template>
  <main class="auth-page">
    <div class="auth-panel">
      <span class="auth-brand">CS JobMate</span>
      <div class="auth-mode" role="group" aria-label="账号操作">
        <button type="button" :class="{ selected: mode === 'login' }" :aria-pressed="mode === 'login'" @click="changeMode('login')">登录</button>
        <button type="button" :class="{ selected: mode === 'register' }" :aria-pressed="mode === 'register'" @click="changeMode('register')">注册</button>
      </div>
      <h1>{{ mode === "login" ? "登录工作区" : "创建账号" }}</h1>
      <form class="auth-form" @submit.prevent="submit">
        <label>
          邮箱
          <input v-model.trim="email" type="email" autocomplete="email" required maxlength="320" />
        </label>
        <label>
          密码
          <input v-model="password" type="password" :autocomplete="mode === 'login' ? 'current-password' : 'new-password'" required minlength="10" maxlength="200" />
        </label>
        <p v-if="errorMessage" class="error-message" role="alert">{{ errorMessage }}</p>
        <button type="submit" :disabled="isSubmitting">{{ isSubmitting ? "正在处理..." : mode === "login" ? "登录" : "创建账号" }}</button>
      </form>
    </div>
  </main>
</template>
