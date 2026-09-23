import { afterEach, describe, expect, it, vi } from "vitest";
import { apiFetch, resolveApiUrl } from "./client";
import { submitEvidenceFeedback } from "./copilot";
import { confirmJobPost, createInterviewReview, getJobTargetTimeline, updateResumeSuggestion } from "./workspace";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("apiFetch", () => {
  it("keeps relative URLs when no API base is configured", () => {
    expect(resolveApiUrl("/api/test")).toBe("/api/test");
  });

  it("prefixes URLs with the configured API base", () => {
    vi.stubEnv("VITE_API_BASE_URL", "http://127.0.0.1:8000/");
    expect(resolveApiUrl("/api/test")).toBe("http://127.0.0.1:8000/api/test");
  });

  it("returns typed JSON for a successful response", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response('{"id": 1}', { status: 200 })));

    await expect(apiFetch<{ id: number }>("/api/test")).resolves.toEqual({ id: 1 });
  });

  it("preserves the FastAPI detail for a failed response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response('{"detail":"简历版本不存在"}', { status: 422 })),
    );

    await expect(apiFetch("/api/test")).rejects.toMatchObject({
      message: "简历版本不存在",
      status: 422,
    });
  });

  it("submits a structured evidence review", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          id: 3,
          turn_id: 7,
          analysis_run_id: 9,
          requirement_id: "req-1",
          verdict: "corrected",
          corrected_status: "partial",
          evidence_ids: ["evidence-1"],
          note: "需要补充量化结果",
          created_at: "2026-07-20 16:00:00",
        }),
        { status: 201 },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      submitEvidenceFeedback(7, {
        requirement_id: "req-1",
        verdict: "corrected",
        corrected_status: "partial",
        evidence_ids: ["evidence-1"],
        note: "需要补充量化结果",
      }),
    ).resolves.toMatchObject({ verdict: "corrected" });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/copilot/turns/7/evidence-feedback",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("surfaces a failed evidence review request", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response('{"detail":"证据不存在"}', { status: 422 })),
    );
    await expect(
      submitEvidenceFeedback(7, { requirement_id: "req-1", verdict: "rejected" }),
    ).rejects.toMatchObject({ message: "证据不存在", status: 422 });
  });
});

describe("application loop API", () => {
  it("loads a target timeline and updates a resume suggestion", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ target: { id: 4 }, events: [], interview_reviews: [] }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ id: 8, status: "accepted" }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getJobTargetTimeline(4)).resolves.toMatchObject({ target: { id: 4 } });
    await expect(updateResumeSuggestion(8, { status: "accepted", edited_text: "" })).resolves.toMatchObject({ status: "accepted" });
    expect(fetchMock).toHaveBeenNthCalledWith(2, "/api/resumes/suggestions/8", expect.objectContaining({ method: "PATCH" }));
  });

  it("confirms an unknown market post before pipeline use", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ id: 9, status: "active" }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(confirmJobPost(9)).resolves.toMatchObject({ id: 9, status: "active" });
    expect(fetchMock).toHaveBeenCalledWith("/api/reports/posts/9/confirm", expect.objectContaining({ method: "POST" }));
  });
  it("submits a structured interview review", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ id: 2, round_number: 1 }), { status: 201 }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(createInterviewReview(4, {
      round_number: 1,
      questions: ["缓存"],
      performance: "mixed",
      feedback: "补充实践",
      result: "pending",
      missing_skills: ["Docker"],
      conclusion: "继续准备",
    })).resolves.toMatchObject({ round_number: 1 });
    expect(fetchMock).toHaveBeenCalledWith("/api/job-targets/4/interview-reviews", expect.objectContaining({ method: "POST" }));
  });
});
