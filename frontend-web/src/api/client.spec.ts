import { afterEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "./client";
import { submitEvidenceFeedback } from "./copilot";

afterEach(() => vi.unstubAllGlobals());

describe("apiFetch", () => {
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
