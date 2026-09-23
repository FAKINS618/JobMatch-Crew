import { apiFetch } from "./client";
import type { ResumeVersion } from "./copilot";

export type ResumeProfile = ResumeVersion["profile"];

export interface ActionItem {
  id: number;
  report_id: number;
  resume_version_id: number | null;
  skill: string;
  title: string;
  priority: "high" | "medium" | "low";
  status: "todo" | "in_progress" | "completed" | "cancelled";
  expected_output: string;
  evidence_count: number;
  source_report_id: number | null;
  source_job_target_id: number | null;
  source_job_title: string;
  source_interview_review_id: number | null;
  source_type: "report" | "interview_review";
  source_turn_id: number | null;
  archived_at: string | null;
  created_at_local: string | null;
  updated_at_local: string | null;
}

export interface JobTarget {
  id: number;
  title: string;
  company: string;
  url: string;
  priority: "A" | "B" | "C";
  match_score: number | null;
  status: "saved" | "applied" | "written_test" | "interview" | "offer" | "rejected" | "withdrawn";
  note: string;
  created_at: string;
  updated_at?: string;
  deadline_at?: string | null;
}

export interface ReportSummary {
  id: number;
  target_role: string;
  score: number | null;
  parse_status: string;
  created_at: string;
  created_at_local: string | null;
  job_post_count: number;
}

export interface JobPost {
  id: number;
  report_id: number;
  title: string;
  company: string;
  url: string;
  content: string;
  source: string;
  status: string;
  freshness_score: number;
  invalid_reason: string;
  relevance_score: number;
  verification_status: string;
  verification_reason: string;
  deadline_at: string | null;
}

export interface ReportDetail extends ReportSummary {
  resume_text: string;
  jd_text: string;
  markdown_report: string;
  parsed_result: string;
  job_posts: JobPost[];
}

export interface ResumeMarketSearchPreference {
  resume_version_id: number;
  auto_search_enabled: boolean;
  city: string;
  updated_at: string | null;
}

export interface ResumeHistoryArtifact {
  id: number;
  turn_id: number;
  artifact_type: "job_brief" | "evidence_map" | "fit_strategy" | "action_bundle";
  payload: Record<string, unknown>;
  status: string;
  created_at: string | null;
}

export interface ResumeHistoryMessage {
  id: number;
  session_id: number;
  turn_id: number | null;
  role: "user" | "assistant";
  content: string;
  created_at: string | null;
}

export interface ResumeHistoryTurn {
  id: number;
  session_id: number;
  status: string;
  stage: string;
  progress: number;
  report_id: number | null;
  input_type: "initial_jd" | "follow_up";
  created_at: string | null;
  updated_at: string | null;
  artifacts: ResumeHistoryArtifact[];
}

export interface ResumeAnalysisHistory {
  resume_version_id: number;
  resume_id: number;
  version_name: string;
  target_role: string;
  created_at: string | null;
  sessions: Array<{
    id: number;
    resume_version_id: number | null;
    target_role: string;
    status: string;
    created_at: string | null;
    updated_at: string | null;
    messages: ResumeHistoryMessage[];
    turns: ResumeHistoryTurn[];
  }>;
  reports: Array<{
    id: number;
    target_role: string;
    score: number | null;
    parse_status: string;
    report_summary: string;
    created_at: string | null;
    created_at_local: string | null;
    job_post_count: number;
  }>;
  market_search_preference: ResumeMarketSearchPreference;
  market_search_triggers: Array<{
    id: number;
    resume_version_id: number;
    source_turn_id: number;
    analysis_task_id: number;
    report_id: number | null;
    target_role: string;
    city: string;
    trigger_mode: "auto" | "manual";
    status: "pending" | "running" | "success" | "failed" | "skipped";
    reason: string;
    created_at: string | null;
    updated_at: string | null;
  }>;
}

export function getResumeAnalysisHistory(resumeVersionId: number) {
  return apiFetch<ResumeAnalysisHistory>(`/api/resumes/versions/${resumeVersionId}/history`);
}

export function getMarketSearchPreference(resumeVersionId: number) {
  return apiFetch<ResumeMarketSearchPreference>(
    `/api/resumes/versions/${resumeVersionId}/market-search-preference`,
  );
}

export function updateMarketSearchPreference(
  resumeVersionId: number,
  payload: { auto_search_enabled: boolean; city: string },
) {
  return apiFetch<ResumeMarketSearchPreference>(
    `/api/resumes/versions/${resumeVersionId}/market-search-preference`,
    { method: "PATCH", body: JSON.stringify(payload) },
  );
}

export function parseResume(rawText: string) {
  return apiFetch<{ profile: ResumeProfile }>("/api/resumes/parse", {
    method: "POST",
    body: JSON.stringify({ raw_text: rawText }),
  });
}

export function saveResumeVersion(payload: {
  version_name: string;
  target_role: string;
  raw_text: string;
  profile: ResumeProfile;
}) {
  return apiFetch<ResumeVersion>("/api/resumes/versions", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function listReports() {
  return apiFetch<{ reports: ReportSummary[] }>("/api/reports");
}

export function getReport(reportId: number) {
  return apiFetch<ReportDetail>(`/api/reports/${reportId}`);
}

export function confirmJobPost(postId: number) {
  return apiFetch<Pick<JobPost, "id" | "report_id" | "status" | "verification_status" | "verification_reason">>(`/api/reports/posts/${postId}/confirm`, {
    method: "POST",
  });
}

export function createJobTarget(payload: { report_id: number; url: string; priority: "A" | "B" | "C" }) {
  return apiFetch<JobTarget>("/api/job-targets", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export interface MarketTaskCreateResponse {
  task_id: number;
  status: "pending" | "running" | "success" | "failed";
}

export interface MarketTask {
  id: number;
  task_type: string;
  status: "pending" | "running" | "success" | "failed";
  progress: number;
  report_id: number | null;
  error_message: string;
}

export function createMarketMatchTask(payload: {
  resume_text: string;
  target_role: string;
  city: string;
  max_results: number;
  resume_version_id: number;
}) {
  return apiFetch<MarketTaskCreateResponse>("/api/tasks/market-match", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getMarketTask(taskId: number) {
  return apiFetch<MarketTask>(`/api/tasks/${taskId}`);
}

export function createActionItemsFromReport(reportId: number, skills: string[]) {
  return apiFetch<ActionItem[]>(`/api/action-items/from-report/${reportId}`, {
    method: "POST",
    body: JSON.stringify({ skills }),
  });
}

export function listActionItems(params: { includeArchived?: boolean } = {}) {
  const query = params.includeArchived ? "?include_archived=true" : "";
  return apiFetch<ActionItem[]>(`/api/action-items${query}`);
}

export function updateActionItem(itemId: number, status: ActionItem["status"]) {
  return apiFetch<ActionItem>(`/api/action-items/${itemId}`, {
    method: "PATCH",
    body: JSON.stringify({ status }),
  });
}

export function archiveActionItem(itemId: number) {
  return apiFetch<ActionItem>(`/api/action-items/${itemId}`, { method: "DELETE" });
}

export function restoreActionItem(itemId: number) {
  return apiFetch<ActionItem>(`/api/action-items/${itemId}/restore`, { method: "POST" });
}
export function createActionEvidence(itemId: number, content: string, url: string) {
  return apiFetch(`/api/action-items/${itemId}/evidence`, {
    method: "POST",
    body: JSON.stringify({
      evidence_type: url ? "link" : "note",
      content,
      url: url || null,
    }),
  });
}

export function listJobTargets() {
  return apiFetch<JobTarget[]>("/api/job-targets");
}

export function updateJobTarget(itemId: number, status: JobTarget["status"], note: string) {
  return apiFetch<JobTarget>(`/api/job-targets/${itemId}`, {
    method: "PATCH",
    body: JSON.stringify({ status, note }),
  });
}

export interface ResumeSuggestion {
  id: number;
  report_id: number;
  resume_version_id: number;
  suggestion_type: string;
  source_context: string;
  suggested_text: string;
  edited_text: string;
  status: "pending" | "accepted" | "rejected" | "edited";
  created_at: string | null;
  updated_at: string | null;
  confirmed_at: string | null;
}

export interface ApplicationEvent {
  id: number;
  job_target_id: number;
  event_type: string;
  occurred_at: string;
  note: string;
}

export interface InterviewReview {
  id: number;
  job_target_id: number;
  report_id: number;
  round_number: number;
  occurred_at: string | null;
  questions: string[];
  performance: "strong" | "mixed" | "needs_work";
  feedback: string;
  result: "pending" | "passed" | "failed" | "unknown";
  missing_skills: string[];
  conclusion: string;
  actions_confirmed_at: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface JobTargetTimeline {
  target: JobTarget;
  events: ApplicationEvent[];
  interview_reviews: InterviewReview[];
}

export function getJobTargetTimeline(targetId: number) {
  return apiFetch<JobTargetTimeline>(`/api/job-targets/${targetId}/timeline`);
}

export function createApplicationEvent(targetId: number, payload: { event_type: string; note: string }) {
  return apiFetch<ApplicationEvent>(`/api/job-targets/${targetId}/events`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function createInterviewReview(targetId: number, payload: {
  round_number: number;
  occurred_at?: string | null;
  questions: string[];
  performance: InterviewReview["performance"];
  feedback: string;
  result: InterviewReview["result"];
  missing_skills: string[];
  conclusion: string;
}) {
  return apiFetch<InterviewReview>(`/api/job-targets/${targetId}/interview-reviews`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateInterviewReview(reviewId: number, payload: Partial<{
  round_number: number;
  occurred_at: string | null;
  questions: string[];
  performance: InterviewReview["performance"];
  feedback: string;
  result: InterviewReview["result"];
  missing_skills: string[];
  conclusion: string;
}>) {
  return apiFetch<InterviewReview>(`/api/job-targets/interview-reviews/${reviewId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function confirmInterviewActions(reviewId: number, skills: string[]) {
  return apiFetch<ActionItem[]>(`/api/job-targets/interview-reviews/${reviewId}/actions`, {
    method: "POST",
    body: JSON.stringify({ skills }),
  });
}

export function listResumeSuggestions(reportId: number) {
  return apiFetch<ResumeSuggestion[]>(`/api/resumes/suggestions/report/${reportId}`);
}

export function updateResumeSuggestion(
  suggestionId: number,
  payload: { status: ResumeSuggestion["status"]; edited_text: string },
) {
  return apiFetch<ResumeSuggestion>(`/api/resumes/suggestions/${suggestionId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function createResumeVersionFromSuggestions(payload: {
  report_id: number;
  source_resume_version_id: number;
  suggestion_ids: number[];
  version_name: string;
  target_role: string;
  raw_text: string;
  profile: ResumeProfile;
}) {
  return apiFetch<ResumeVersion>("/api/resumes/versions/from-suggestions", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export type NextActionType =
  | "resume_suggestions"
  | "resume_version_create"
  | "job_target_apply"
  | "job_target_follow_up"
  | "interview_review"
  | "interview_actions"
  | "analysis_progress"
  | "analysis_retry";

export interface NextAction {
  id: string;
  action_type: NextActionType;
  priority: "high" | "medium" | "low";
  title: string;
  description: string;
  entity_type: "report" | "resume_version" | "job_target" | "interview_review" | "analysis_task" | "analysis_turn";
  entity_id: number;
  route: string;
  action_label: string;
  due_at: string | null;
  due_at_local?: string | null;
  updated_at: string | null;
  updated_at_local?: string | null;
}

export function getNextActions() {
  return apiFetch<NextAction[]>("/api/dashboard/next-actions");
}
