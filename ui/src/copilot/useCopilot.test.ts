import { describe, expect, it } from "vitest";
import { parseSseBuffer } from "./useCopilot";

describe("parseSseBuffer", () => {
  it("parses a complete event", () => {
    const { events, rest } = parseSseBuffer('event: text\ndata: {"text":"hello"}\n\n');
    expect(events).toEqual([{ event: "text", data: { text: "hello" } }]);
    expect(rest).toBe("");
  });

  it("parses several events in one chunk", () => {
    const raw =
      'event: tool\ndata: {"name":"rank_entities"}\n\n' + 'event: text\ndata: {"text":"ok"}\n\n';
    const { events } = parseSseBuffer(raw);
    expect(events.map((e) => e.event)).toEqual(["tool", "text"]);
  });

  it("holds back a partial event until the rest arrives", () => {
    // Network chunks split wherever they like; parsing a half-received frame
    // would drop it entirely.
    const first = parseSseBuffer('event: text\ndata: {"te');
    expect(first.events).toEqual([]);
    expect(first.rest).toBe('event: text\ndata: {"te');

    const second = parseSseBuffer(first.rest + 'xt":"hello"}\n\n');
    expect(second.events).toEqual([{ event: "text", data: { text: "hello" } }]);
  });

  it("skips a malformed frame without killing the stream", () => {
    // The rest of the answer is still worth showing.
    const raw = "event: text\ndata: {not json}\n\n" + 'event: text\ndata: {"text":"fine"}\n\n';
    const { events } = parseSseBuffer(raw);
    expect(events).toEqual([{ event: "text", data: { text: "fine" } }]);
  });

  it("ignores frames with no data line", () => {
    const { events } = parseSseBuffer(": keepalive comment\n\n");
    expect(events).toEqual([]);
  });

  it("carries a view_state patch through intact", () => {
    const raw =
      'event: view_state\ndata: {"patch":{"view":"dashboard","dashboard":{"entityIds":[274]}},"explanation":"Plotted it."}\n\n';
    const { events } = parseSseBuffer(raw);
    const data = events[0].data as Record<string, unknown>;
    expect(data.patch).toEqual({ view: "dashboard", dashboard: { entityIds: [274] } });
    expect(data.explanation).toBe("Plotted it.");
  });
});
