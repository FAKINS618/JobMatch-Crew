import { apiFetch } from "./client";

export type ArtifactType = "job_brief" | "evidence_map" | "fit_strategy" | "action_bundle";
export type TurnStatus = "pending" | "running" | "completed" | "failed";
export type TurnInputType = "initial_jd" | "follow_up";

export interface ResumeVersion {
  id: number;
  version_name: string;
  target_role: string;
  raw_text: string;
  profile: {
    education: string[];
    skills: string[];
    projects: Array<{
      name: string;
      role: string;
      technologies: string[];
      description: string;
      achievements: string[];
    }>;
    internships: string[];
    awards: string[];
    target_roles: string[];
    available_from: string;
    parse_notes: string[];
  };
  created_at: string | null;
}

export interface CopilotMessage {
  id: number;
  session_id: number;
  role: "user" | "assistant";
  content: string;
  turn_id: number | null;
  created_at: string;
}

export interface Artifact {
  id: number;
  turn_id: number;
  artifact_type: ArtifactType;
  payload: Record<string, unknown>;
  status: string;
  created_at: string;
}

export interface AnalysisTurn {
  id: number;
  session_id: number;
  status: TurnStatus;
  stage: string;
  progress: number;
  error_message: string;
  report_id: number | null;
  parent_turn_id: number | null;
  input_type: TurnInputType;
  created_at: string;
  updated_at: string;
  artifacts: Artifact[];
}

export interface CopilotSession {
  id: number;
  resume_version_id: number | null;
  active_report_id: number | null;
  target_role: string;
  status: string;
  created_at: string;
  updated_at: string;
  messages: CopilotMessage[];
  turns: AnalysisTurn[];
}

export function listResumeVersions(): Promise<ResumeVersion[]> {
  return apiFetch("/api/resumes/versions");
}

export function createSession(payload: { resume_version_id?: number; target_role?: string }) {
  return apiFetch<CopilotSession>("/api/v1/copilot/sessions", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getSession(sessionId: number) {
  return apiFetch<CopilotSession>(`/api/v1/copilot/sessions/${sessionId}`);
}

export function sendMessage(sessionId: number, content: string) {
  return apiFetch<AnalysisTurn>(`/api/v1/copilot/sessions/${sessionId}/messages`, {
    method: "POST",
    body: JSON.stringify({ content }),
  });
}

export function getTurn(turnId: number) {
  return apiFetch<AnalysisTurn>(`/api/v1/copilot/turns/${turnId}`);
}

export function decideArtifact(
  artifactId: number,
  decision: "accept" | "reject" | "ask" | "create_task",
) {
  return apiFetch(`/api/v1/copilot/artifacts/${artifactId}/decisions`, {
    method: "POST",
    body: JSON.stringify({ decision }),
  });
}
