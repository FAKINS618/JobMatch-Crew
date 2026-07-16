import { createRouter, createWebHistory } from "vue-router";
import CopilotView from "./views/CopilotView.vue";
import ResumeView from "./views/ResumeView.vue";
import ActionPlanView from "./views/ActionPlanView.vue";
import PipelineView from "./views/PipelineView.vue";
import JobInboxView from "./views/JobInboxView.vue";

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", name: "copilot", component: CopilotView },
    { path: "/resumes", name: "resumes", component: ResumeView },
    { path: "/actions", name: "actions", component: ActionPlanView },
    { path: "/pipeline", name: "pipeline", component: PipelineView },
    { path: "/inbox", name: "inbox", component: JobInboxView },
  ],
});
