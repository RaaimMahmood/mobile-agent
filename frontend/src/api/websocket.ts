import { getApiKey } from './apiKey'

// ── WebSocket event types ──────────────────────────────────────────────────────

export interface ScreenshotUpdateEvent {
  type: 'screenshot_update'
  screenshot: string
  round: number
  element_count: number
}

export interface ActionEvent {
  type: 'action_event'
  round: number
  action: string
  element_id: number | null
  thought: string
  observation: string
}

export interface KBUpdateEvent {
  type: 'kb_update'
  doc: {
    id: string
    documentation: string
    element_sig: string
    app_name: string
  }
}

export interface PlanReadyEvent {
  type: 'plan_ready'
  steps: string[]
}

export interface StatusChangeEvent {
  type: 'status_change'
  status: 'running' | 'done' | 'error' | 'paused'
  task_complete?: boolean
  failure_reason?: string
  kb_count?: number
  mode?: string
}

export interface ErrorEvent {
  type: 'error'
  message: string
}

export interface ConfirmationRequiredEvent {
  type: 'confirmation_required'
  risk: 'medium' | 'high'
  action: string
  element_id: number | null
  thought: string
}

export interface ActionBlockedEvent {
  type: 'action_blocked'
  risk: 'critical'
  action: string
  reason: string
}

export type AgentEvent =
  | ScreenshotUpdateEvent
  | ActionEvent
  | KBUpdateEvent
  | PlanReadyEvent
  | StatusChangeEvent
  | ErrorEvent
  | ConfirmationRequiredEvent
  | ActionBlockedEvent

// ── WebSocket client ───────────────────────────────────────────────────────────

type EventHandler = (event: AgentEvent) => void

export class AgentWebSocket {
  private ws: WebSocket | null = null
  private sessionId: string
  private handler: EventHandler
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null

  constructor(sessionId: string, handler: EventHandler) {
    this.sessionId = sessionId
    this.handler = handler
  }

  connect(): void {
    const protocol = location.protocol === 'https:' ? 'wss' : 'ws'
    const apiKey = getApiKey()
    const tokenParam = apiKey ? `?token=${encodeURIComponent(apiKey)}` : ''
    const url = `${protocol}://${location.host}/ws/${this.sessionId}${tokenParam}`
    this.ws = new WebSocket(url)

    this.ws.onmessage = (e) => {
      try {
        const event = JSON.parse(e.data) as AgentEvent
        this.handler(event)
      } catch {
        // ignore malformed messages
      }
    }

    this.ws.onclose = () => {
      this.reconnectTimer = setTimeout(() => this.connect(), 2000)
    }
  }

  send(msg: { type: string }): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(msg))
    }
  }

  disconnect(): void {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer)
    this.ws?.close()
    this.ws = null
  }
}
