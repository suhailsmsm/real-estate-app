import { beforeEach, describe, expect, it, vi } from "vitest";
import { clearTokens, getAccessToken, setAccessToken, setRefreshToken } from "./auth";
import { ApiError, apiGet, restoreSession, toQueryString } from "./client";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  clearTokens();
});

describe("toQueryString", () => {
  it("omits null, undefined and empty values", () => {
    expect(toQueryString({ a: 1, b: null, c: undefined, d: "", e: false })).toBe("?a=1&e=false");
  });

  it("returns an empty string when nothing is set", () => {
    expect(toQueryString({ a: null })).toBe("");
  });
});

describe("apiGet", () => {
  it("sends the bearer token when authenticated", async () => {
    setAccessToken("tok-123");
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ items: [] }));
    vi.stubGlobal("fetch", fetchMock);

    await apiGet("/dimensions/areas", { limit: 5 });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/dimensions/areas?limit=5");
    expect((init.headers as Headers).get("Authorization")).toBe("Bearer tok-123");
  });

  it("surfaces the API's own error envelope, including ambiguity candidates", async () => {
    // The area-code migration means an old code can have several successors;
    // the UI needs the candidate list to let the user pick, so it must survive
    // error handling rather than being flattened to a message string.
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(
          {
            error: "ambiguous_entity",
            message: "Area 20 was subdivided into 4 current areas",
            old_area_id: 20,
            candidates: [
              { id: 292, dld_area_code: "C-44", name_en: "DUBAI MARINA" },
              { id: 374, dld_area_code: "C-74", name_en: "JUMEIRAH BEACH RESIDENCE" },
            ],
          },
          422,
        ),
      ),
    );

    const err = (await apiGet("/facts/transactions", { area_id: 20 }).catch((e) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.code).toBe("ambiguous_entity");
    expect(err.status).toBe(422);
    expect(err.ambiguousCandidates).toHaveLength(2);
    expect(err.ambiguousCandidates[0].name_en).toBe("DUBAI MARINA");
  });

  it("reads FastAPI's validation envelope", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({ detail: [{ msg: "Input should be <= 200", loc: ["query", "limit"] }] }, 422),
      ),
    );
    const err = (await apiGet("/dimensions/areas", { limit: 999 }).catch((e) => e)) as ApiError;
    expect(err.message).toContain("Input should be <= 200");
    expect(err.message).toContain("query.limit");
  });

  it("refreshes once on 401 and retries the original request", async () => {
    setAccessToken("stale");
    setRefreshToken("refresh-abc");

    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ error: "unauthorized" }, 401))
      .mockResolvedValueOnce(jsonResponse({ access_token: "fresh" }))
      .mockResolvedValueOnce(jsonResponse({ items: [{ id: 1 }] }));
    vi.stubGlobal("fetch", fetchMock);

    const out = await apiGet<{ items: unknown[] }>("/dimensions/areas");

    expect(out.items).toHaveLength(1);
    expect(getAccessToken()).toBe("fresh");
    expect(fetchMock).toHaveBeenCalledTimes(3);
    // The retry must carry the NEW token, not the stale one.
    const retryInit = fetchMock.mock.calls[2][1];
    expect((retryInit.headers as Headers).get("Authorization")).toBe("Bearer fresh");
  });

  it("does not loop when the refreshed token is also rejected", async () => {
    setAccessToken("stale");
    setRefreshToken("refresh-abc");
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({}, 401))
      .mockResolvedValueOnce(jsonResponse({ access_token: "fresh" }))
      .mockResolvedValueOnce(jsonResponse({}, 401));
    vi.stubGlobal("fetch", fetchMock);

    await expect(apiGet("/dimensions/areas")).rejects.toBeInstanceOf(ApiError);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("clears tokens when the refresh token itself is rejected", async () => {
    setAccessToken("stale");
    setRefreshToken("expired");
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(jsonResponse({}, 401))
        .mockResolvedValueOnce(jsonResponse({ error: "invalid" }, 401)),
    );

    await expect(apiGet("/dimensions/areas")).rejects.toBeInstanceOf(ApiError);
    expect(getAccessToken()).toBeNull();
    expect(localStorage.getItem("dxb.refresh_token")).toBeNull();
  });

  it("refreshes only once when several requests 401 together", async () => {
    // React Query refetches panels in parallel; each must not start its own
    // refresh, or they race and invalidate each other's fresh token.
    setAccessToken("stale");
    setRefreshToken("refresh-abc");

    let refreshCalls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (String(url).endsWith("/auth/refresh")) {
          refreshCalls += 1;
          return Promise.resolve(jsonResponse({ access_token: "fresh" }));
        }
        return Promise.resolve(
          getAccessToken() === "fresh" ? jsonResponse({ items: [] }) : jsonResponse({}, 401),
        );
      }),
    );

    await Promise.all([apiGet("/a"), apiGet("/b"), apiGet("/c")]);
    expect(refreshCalls).toBe(1);
  });
});

describe("restoreSession", () => {
  it("is false with nothing stored", async () => {
    expect(await restoreSession()).toBe(false);
  });

  it("trades a stored refresh token for a fresh access token", async () => {
    setRefreshToken("refresh-abc");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ access_token: "fresh" })));
    expect(await restoreSession()).toBe(true);
    expect(getAccessToken()).toBe("fresh");
  });
});
