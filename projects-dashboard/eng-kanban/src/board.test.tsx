import { render, screen } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"
import { KanbanBoard } from "@/components/kanban-board"
import { commitDrag, type BoardPayload, type IssueCard } from "@/lib/board-state"

const columns: BoardPayload["columns"] = [
  { id: "backlog", title: "Backlog", droppable: true },
  { id: "ready", title: "Ready", droppable: true },
  { id: "in_progress", title: "In Progress", droppable: true },
  { id: "pending_review", title: "Pending Review", droppable: true },
  { id: "done", title: "Done", droppable: false },
]

function card(number: number, column = "backlog"): IssueCard {
  return {
    number,
    title: `Issue ${number}`,
    url: `https://github.com/cvolkernick/personal-workspace/issues/${number}`,
    column,
    conflict: false,
    badges: [],
    worker: null,
    chips: [],
    pullRequests: [],
  }
}

function board(cards: IssueCard[], writeEnabled: boolean): BoardPayload {
  return {
    ok: true,
    writeEnabled,
    repo: "cvolkernick/personal-workspace",
    columns,
    cards,
  }
}

describe("eng board", () => {
  it("renders all 250 cards from a fixture", () => {
    const cards = Array.from({ length: 250 }, (_, index) => card(index + 1))
    render(<KanbanBoard board={board(cards, false)} notice={null} onMove={vi.fn()} />)
    expect(document.querySelectorAll("[data-issue]")).toHaveLength(250)
    expect(screen.getByRole("link", { name: /#250/ })).toBeTruthy()
  })

  it("does not render drag handles or call the move client when write mode is off", () => {
    const onMove = vi.fn()
    render(
      <KanbanBoard
        board={board([card(8, "ready")], false)}
        notice={null}
        onMove={onMove}
      />,
    )
    expect(screen.queryByRole("button", { name: /drag #/i })).toBeNull()
    expect(onMove).not.toHaveBeenCalled()
    expect(document.querySelector('[data-column="done"]')?.getAttribute("data-droppable")).toBe(
      "false",
    )
  })

  it("renders a drag handle when write mode is on", () => {
    render(
      <KanbanBoard
        board={board([card(8, "ready")], true)}
        notice={null}
        onMove={vi.fn()}
      />,
    )
    expect(screen.getByRole("button", { name: "Drag #8" })).toBeTruthy()
  })

  it("dragging Ready to In Progress requests one column change", async () => {
    const onMove = vi.fn().mockResolvedValue(undefined)
    const cards = [card(8, "ready")]
    const result = await commitDrag({
      cards,
      columns,
      activeId: "8",
      overId: "in_progress",
      writeEnabled: true,
      moveIssue: onMove,
    })
    expect(onMove).toHaveBeenCalledTimes(1)
    expect(onMove).toHaveBeenCalledWith({ number: 8, column: "in_progress" })
    expect(result.cards[0]?.column).toBe("in_progress")
    expect(result.error).toBeNull()
  })

  it("a failed drag moves the card back and shows an error", async () => {
    const onMove = vi.fn().mockRejectedValue(new Error("GitHub 500"))
    const cards = [card(8, "ready")]
    const result = await commitDrag({
      cards,
      columns,
      activeId: "8",
      overId: "in_progress",
      writeEnabled: true,
      moveIssue: onMove,
    })
    expect(result.cards).toEqual(cards)
    expect(result.error).toBe("GitHub 500")
    render(
      <KanbanBoard board={board(result.cards, true)} notice={result.error} onMove={onMove} />,
    )
    expect(screen.getByRole("alert").textContent).toContain("GitHub 500")
    expect(document.querySelector('[data-column="ready"] [data-issue="8"]')).toBeTruthy()
  })

  it("rejects a drop on Done and does not ask for a move", async () => {
    const onMove = vi.fn()
    const cards = [card(8, "ready")]
    const result = await commitDrag({
      cards,
      columns,
      activeId: "8",
      overId: "done",
      writeEnabled: true,
      moveIssue: onMove,
    })
    expect(onMove).not.toHaveBeenCalled()
    expect(result.cards).toEqual(cards)
    expect(result.error).toBe("Done is not a drop target")
  })

  it("read-only drag does not call the move client", async () => {
    const onMove = vi.fn()
    const result = await commitDrag({
      cards: [card(8, "ready")],
      columns,
      activeId: "8",
      overId: "in_progress",
      writeEnabled: false,
      moveIssue: onMove,
    })
    expect(onMove).not.toHaveBeenCalled()
    expect(result.request).toBeNull()
  })
})
