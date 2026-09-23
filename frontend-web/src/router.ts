import { createRouter, createWebHistory } from "vue-router";
import CopilotView from "./views/CopilotView.vue";
import WorkspaceView from "./views/WorkspaceView.vue";
import ResumeView from "./views/ResumeView.vue";
import ResumeReviewView from "./views/ResumeReviewView.vue";
import ActionPlanView from "./views/ActionPlanView.vue";
import PipelineView from "./views/PipelineView.vue";
import JobInboxView from "./views/JobInboxView.vue";

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", name: "workspace", component: WorkspaceView },
    { path: "/copilot", name: "copilot", component: CopilotView },
    { path: "/resumes", name: "resumes", component: ResumeView },
    { path: "/resumes/review", name: "resume-review", component: ResumeReviewView },
    { path: "/actions", name: "actions", component: ActionPlanView },
    { path: "/pipeline", name: "pipeline", component: PipelineView },
    { path: "/inbox", name: "inbox", component: JobInboxView },
  ],
});
