export type PullRequest = {
  number: number
  state: string
  url: string
}

export type Chip = {
  name: string
  kind: string
}

export type IssueCard = {
  number: number
  title: string
  url: string
  column: string
  conflict: boolean
  badges: string[]
  worker: string | null
  chips: Chip[]
  pullRequests: PullRequest[]
}

export type BoardColumn = {
  id: string
  title: string
  droppable: boolean
}

export type BoardPayload = {
  ok: boolean
  writeEnabled: boolean
  repo: string
  columns: BoardColumn[]
  cards: IssueCard[]
  error?: string
}

export type MoveRequest = {
  number: number
  column: string
}

export type DropOutcome = {
  cards: IssueCard[]
  error: string | null
  request: MoveRequest | null
}

export function planDrop(
  cards: IssueCard[],
  columns: BoardColumn[],
  activeId: string,
  overId: string | null,
  writeEnabled: boolean,
): DropOutcome {
  const number = Number(activeId)
  const current = cards.find((card) => card.number === number)
  if (!current || !overId) {
    return { cards, error: null, request: null }
  }
  if (!writeEnabled) {
    return { cards, error: "read-only", request: null }
  }
  const column = columns.find((item) => item.id === overId)
  if (!column || !column.droppable || overId === "done") {
    return { cards, error: "Done is not a drop target", request: null }
  }
  if (current.column === overId) {
    return { cards, error: null, request: null }
  }
  return {
    cards: cards.map((card) =>
      card.number === number ? { ...card, column: overId } : card,
    ),
    error: null,
    request: { number, column: overId },
  }
}

export async function commitDrag(input: {
  cards: IssueCard[]
  columns: BoardColumn[]
  activeId: string
  overId: string | null
  writeEnabled: boolean
  moveIssue: (request: MoveRequest) => Promise<void>
}): Promise<DropOutcome> {
  const planned = planDrop(
    input.cards,
    input.columns,
    input.activeId,
    input.overId,
    input.writeEnabled,
  )
  if (!planned.request) {
    return planned
  }
  try {
    await input.moveIssue(planned.request)
    return planned
  } catch (error) {
    const message = error instanceof Error ? error.message : "move failed"
    return { cards: input.cards, error: message, request: planned.request }
  }
}
