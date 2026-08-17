import { fireEvent, render, renderHook, screen, act } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { CollapseToggle, DateField, useCollapse } from "./components";

describe("DateField", () => {
  it("renders empty for a null value", () => {
    render(<DateField label="From" value={null} onChange={vi.fn()} />);
    expect(screen.getByLabelText("From")).toHaveValue("");
  });

  it("renders an ISO date directly, day-precise", () => {
    render(<DateField label="From" value="2015-03-17" onChange={vi.fn()} />);
    expect(screen.getByLabelText("From")).toHaveValue("2015-03-17");
  });

  it("passes the picked date straight through, unmodified", () => {
    // No forcing to the 1st of the month: month_from/month_to on the API
    // accept any date, and normalizing here would silently disagree with
    // what the user actually selected.
    const onChange = vi.fn();
    render(<DateField label="From" value={null} onChange={onChange} />);
    fireEvent.change(screen.getByLabelText("From"), { target: { value: "2018-11-23" } });
    expect(onChange).toHaveBeenCalledWith("2018-11-23");
  });

  it("clearing the field emits null, not an empty string", () => {
    const onChange = vi.fn();
    render(<DateField label="From" value="2018-06-01" onChange={onChange} />);
    fireEvent.change(screen.getByLabelText("From"), { target: { value: "" } });
    expect(onChange).toHaveBeenCalledWith(null);
  });

  it("is a real <input type='date'>, matching the listing view's own filter", () => {
    render(<DateField label="From" value={null} onChange={vi.fn()} />);
    expect(screen.getByLabelText("From")).toHaveAttribute("type", "date");
  });
});

describe("useCollapse", () => {
  it("defaults open", () => {
    const { result } = renderHook(() => useCollapse());
    expect(result.current[0]).toBe(true);
  });

  it("honors an explicit default", () => {
    const { result } = renderHook(() => useCollapse(false));
    expect(result.current[0]).toBe(false);
  });

  it("toggles on each call", () => {
    const { result } = renderHook(() => useCollapse(true));
    act(() => result.current[1]());
    expect(result.current[0]).toBe(false);
    act(() => result.current[1]());
    expect(result.current[0]).toBe(true);
  });
});

describe("CollapseToggle", () => {
  it("labels itself by the current state, not a static caption", () => {
    const { rerender } = render(<CollapseToggle open={true} onClick={vi.fn()} label="filters" />);
    expect(screen.getByRole("button")).toHaveTextContent("Hide filters");
    rerender(<CollapseToggle open={false} onClick={vi.fn()} label="filters" />);
    expect(screen.getByRole("button")).toHaveTextContent("Show filters");
  });

  it("exposes its state to assistive tech via aria-expanded", () => {
    render(<CollapseToggle open={false} onClick={vi.fn()} label="filters" />);
    expect(screen.getByRole("button")).toHaveAttribute("aria-expanded", "false");
  });

  it("fires onClick", () => {
    const onClick = vi.fn();
    render(<CollapseToggle open={true} onClick={onClick} label="filters" />);
    fireEvent.click(screen.getByRole("button"));
    expect(onClick).toHaveBeenCalledOnce();
  });
});
