import { useEffect, useState } from "react"
import {
  DndContext,
  PointerSensor,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core"
import { GripVertical } from "lucide-react"
import { ThemeProvider } from "next-themes"
import { toast } from "sonner"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Toaster } from "@/components/ui/sonner"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import {
  commitDrag,
  type BoardColumn,
  type BoardPayload,
  type IssueCard,
  type MoveRequest,
} from "@/lib/board-state"

const TONE: Record<string, string> = {
  backlog: "bg-muted-foreground",
  ready: "bg-sky-500",
  in_progress: "bg-amber-400",
  pending_review: "bg-violet-400",
  done: "bg-emerald-400",
}

function IssueCardView({
  card,
  writeEnabled,
}: {
  card: IssueCard
  writeEnabled: boolean
}) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: String(card.number),
    disabled: !writeEnabled,
  })

  return (
    <Card
      ref={setNodeRef}
      data-issue={card.number}
      size="sm"
      className={isDragging ? "opacity-60" : undefined}
    >
      <CardHeader className="grid-cols-[auto_minmax(0,1fr)]">
        {writeEnabled ? (
          <Button
            type="button"
            variant="ghost"
            size="icon-xs"
            aria-label={`Drag #${card.number}`}
            {...listeners}
            {...attributes}
          >
            <GripVertical />
          </Button>
        ) : null}
        <CardTitle className="min-w-0 flex-1 text-left text-sm leading-snug">
          <a href={card.url} className="hover:underline">
            <span className="text-muted-foreground">#{card.number}</span>{" "}
            {card.title}
          </a>
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-2 text-left">
        {card.worker ? (
          <Tooltip>
            <TooltipTrigger className="w-fit text-xs text-muted-foreground">
              {card.worker}
            </TooltipTrigger>
            <TooltipContent>Worker</TooltipContent>
          </Tooltip>
        ) : null}
        {card.badges.length > 0 || card.chips.length > 0 ? (
          <div className="flex flex-wrap gap-1">
            {card.badges.map((badge) => (
              <Badge
                key={badge}
                variant={badge === "label conflict" ? "destructive" : "outline"}
              >
                {badge}
              </Badge>
            ))}
            {card.chips.map((chip) => (
              <Badge key={chip.name} variant="secondary">
                {chip.name}
              </Badge>
            ))}
          </div>
        ) : null}
        {card.pullRequests.length > 0 ? (
          <div className="flex flex-wrap gap-2 text-xs">
            {card.pullRequests.map((pull) => (
              <a key={pull.number} href={pull.url} className="hover:underline">
                PR #{pull.number} {pull.state}
              </a>
            ))}
          </div>
        ) : null}
      </CardContent>
    </Card>
  )
}

function ColumnView({
  column,
  cards,
  writeEnabled,
}: {
  column: BoardColumn
  cards: IssueCard[]
  writeEnabled: boolean
}) {
  const droppable = writeEnabled && column.droppable && column.id !== "done"
  const { setNodeRef, isOver } = useDroppable({
    id: column.id,
    disabled: !droppable,
  })
  return (
    <section
      ref={setNodeRef}
      data-column={column.id}
      data-droppable={droppable ? "true" : "false"}
      className={`flex w-72 shrink-0 flex-col rounded-xl bg-muted/40 ring-1 ring-foreground/10 ${isOver ? "ring-primary" : ""}`}
    >
      <header className="flex items-center gap-2 px-3 py-2">
        <span className={`size-2 rounded-full ${TONE[column.id] ?? "bg-muted-foreground"}`} />
        <h2 className="text-sm font-medium">{column.title}</h2>
        <Badge variant="outline">{cards.length}</Badge>
      </header>
      <ScrollArea className="h-[calc(100svh-8.5rem)] px-2 pb-2">
        <div className="flex flex-col gap-2">
          {cards.map((card) => (
            <IssueCardView key={card.number} card={card} writeEnabled={writeEnabled} />
          ))}
        </div>
      </ScrollArea>
    </section>
  )
}

export function KanbanBoard({
  board,
  notice,
  onMove,
  onRefresh,
}: {
  board: BoardPayload
  notice: string | null
  onMove: (request: MoveRequest) => Promise<void>
  onRefresh?: () => void
}) {
  const [cards, setCards] = useState(board.cards)
  const [error, setError] = useState<string | null>(notice)
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }))

  useEffect(() => {
    setCards(board.cards)
  }, [board])

  useEffect(() => {
    setError(notice)
  }, [notice])

  async function onDragEnd(event: DragEndEvent) {
    const result = await commitDrag({
      cards,
      columns: board.columns,
      activeId: String(event.active.id),
      overId: event.over ? String(event.over.id) : null,
      writeEnabled: board.writeEnabled,
      moveIssue: onMove,
    })
    setCards(result.cards)
    setError(result.error)
    if (result.error) toast.error(result.error)
  }

  const columns = (
    <div className="flex min-h-0 flex-1 gap-3 overflow-x-auto p-3">
      {board.columns.map((column) => (
        <ColumnView
          key={column.id}
          column={column}
          cards={cards.filter((card) => card.column === column.id)}
          writeEnabled={board.writeEnabled}
        />
      ))}
    </div>
  )

  return (
    <ThemeProvider attribute="class" defaultTheme="dark" enableSystem={false}>
      <TooltipProvider>
        <div className="flex h-svh flex-col bg-background text-foreground">
          <header className="flex flex-wrap items-center justify-between gap-3 border-b px-4 py-3">
            <div>
              <h1 className="text-lg font-semibold tracking-tight">Eng backlog</h1>
              <p className="text-xs text-muted-foreground">{board.repo}</p>
            </div>
            <div className="flex items-center gap-2">
              <Badge variant="outline">{board.writeEnabled ? "Write" : "Read only"}</Badge>
              <Button type="button" variant="outline" onClick={onRefresh}>
                Refresh
              </Button>
              <Button variant="ghost" asChild>
                <a href="/">Workflow</a>
              </Button>
            </div>
          </header>
          {error ? (
            <div role="alert" className="border-b border-destructive/40 bg-destructive/10 px-4 py-2 text-sm text-destructive">
              {error}
            </div>
          ) : null}
          {board.writeEnabled ? (
            <DndContext sensors={sensors} onDragEnd={onDragEnd}>
              {columns}
            </DndContext>
          ) : (
            columns
          )}
        </div>
        <Toaster />
      </TooltipProvider>
    </ThemeProvider>
  )
}
