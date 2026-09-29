import { afterEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import App from "../App.vue";
import AuthView from "../views/AuthView.vue";
import { apiFetch, resolveApiUrl } from "./client";
import { submitEvidenceFeedback } from "./copilot";
import { getCurrentUser } from "./auth";
import { confirmJobPost, createInterviewReview, getJobTargetTimeline, updateResumeSuggestion } from "./workspace";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  window.localStorage.clear();
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

  it("clears an expired token and announces that authentication is required", async () => {
    window.localStorage.setItem("cs-jobmate-access-token", "expired-token");
    const expired = vi.fn();
    window.addEventListener("jobmatch-auth-expired", expired, { once: true });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response('{"detail":"需要登录后访问"}', { status: 401 })));

    await expect(getCurrentUser()).rejects.toMatchObject({ status: 401 });
    expect(window.localStorage.getItem("cs-jobmate-access-token")).toBeNull();
    expect(expired).toHaveBeenCalledOnce();
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

describe("account screens", () => {
  it("requires sign in before rendering a protected workspace", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response('{"auth_enabled":true}', { status: 200 })));
    const wrapper = mount(App, { global: { stubs: { RouterLink: true, RouterView: true } } });
    await flushPromises();
    expect(wrapper.find(".auth-mode").exists()).toBe(true);
    expect(wrapper.find(".side-nav").exists()).toBe(false);
    wrapper.unmount();
  });

  it("keeps the local workspace available when authentication is disabled", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response('{"auth_enabled":false}', { status: 200 })));
    const wrapper = mount(App, { global: { stubs: { RouterLink: true, RouterView: true } } });
    await flushPromises();
    expect(wrapper.find(".side-nav").exists()).toBe(true);
    wrapper.unmount();
  });

  it("logs in and stores the access token", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      access_token: "signed-token", token_type: "bearer",
      user: { id: 2, email: "user@example.com", created_at: null },
    }), { status: 200 })));
    const wrapper = mount(AuthView);
    await wrapper.get('input[type="email"]').setValue("user@example.com");
    await wrapper.get('input[type="password"]').setValue("long-password");
    await wrapper.get("form").trigger("submit");
    await flushPromises();
    expect(window.localStorage.getItem("cs-jobmate-access-token")).toBe("signed-token");
    expect(wrapper.emitted("authenticated")).toHaveLength(1);
  });

  it("shows registration errors without storing a token", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response('{"detail":"邮箱已注册"}', { status: 409 })));
    const wrapper = mount(AuthView);
    await wrapper.findAll(".auth-mode button")[1].trigger("click");
    await wrapper.get('input[type="email"]').setValue("used@example.com");
    await wrapper.get('input[type="password"]').setValue("long-password");
    await wrapper.get("form").trigger("submit");
    await flushPromises();
    expect(wrapper.get('[role="alert"]').text()).toContain("邮箱已注册");
    expect(window.localStorage.getItem("cs-jobmate-access-token")).toBeNull();
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
