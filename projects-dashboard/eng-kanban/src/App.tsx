import { useCallback, useEffect, useState } from "react"
import { KanbanBoard } from "@/components/kanban-board"
import type { BoardPayload, MoveRequest } from "@/lib/board-state"

async function readBoard(): Promise<BoardPayload> {
  const response = await fetch("/api/kanban")
  const payload = (await response.json()) as BoardPayload
  if (!response.ok || payload.ok === false) {
    throw new Error(payload.error || "Could not load the board")
  }
  return payload
}

async function postMove(request: MoveRequest): Promise<void> {
  const response = await fetch("/api/kanban/move", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ number: request.number, column: request.column }),
  })
  const payload = (await response.json()) as { ok?: boolean; error?: string }
  if (!response.ok || payload.ok === false) {
    throw new Error(payload.error || "Move failed")
  }
}

export default function App() {
  const [board, setBoard] = useState<BoardPayload | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const reload = useCallback(() => {
    readBoard()
      .then((payload) => {
        setBoard(payload)
        setNotice(null)
      })
      .catch((error: unknown) => {
        setNotice(error instanceof Error ? error.message : "Could not load the board")
      })
  }, [])

  useEffect(() => {
    reload()
  }, [reload])

  if (!board) {
    return (
      <div className="flex h-svh items-center justify-center bg-background text-sm text-muted-foreground">
        {notice ? (
          <div role="alert">{notice}</div>
        ) : (
          <p>Loading eng backlog…</p>
        )}
      </div>
    )
  }

  return (
    <KanbanBoard
      board={board}
      notice={notice}
      onMove={postMove}
      onRefresh={reload}
    />
  )
}
