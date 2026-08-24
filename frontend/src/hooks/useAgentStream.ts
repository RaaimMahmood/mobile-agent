import { useEffect, useRef } from 'react'
import { AgentWebSocket, type AgentEvent, type ConfirmationRequiredEvent } from '../api/websocket'
import { useAgentStore } from '../store/agentStore'
import { agentApi } from '../api/client'

// Matches the existing useDevice(4000) polling cadence in this codebase.
const RECONCILE_POLL_MS = 4000

export function useAgentStream(sessionId: string | null) {
  const {
    setScreenshot,
    appendLog,
    appendKbDoc,
    setAgentStatus,
    setTaskComplete,
    setFailureReason,
    setPlan,
    setRoundNum,
    setPendingConfirmation,
  } = useAgentStore()

  const wsRef = useRef<AgentWebSocket | null>(null)

  useEffect(() => {
    if (!sessionId) return

    const handler = (event: AgentEvent) => {
      switch (event.type) {
        case 'screenshot_update':
          setScreenshot(event.screenshot, event.round)
          setRoundNum(event.round)
          break
        case 'action_event':
          appendLog(event)
          break
        case 'kb_update':
          appendKbDoc(event.doc)
          break
        case 'plan_ready':
          setPlan(event.steps)
          break
        case 'status_change':
          setAgentStatus(event.status)
          if (event.task_complete !== undefined) setTaskComplete(event.task_complete)
          if (event.failure_reason) setFailureReason(event.failure_reason)
          break
        case 'error':
          setAgentStatus('error')
          break
        case 'confirmation_required':
          setPendingConfirmation(event)
          break
        case 'action_blocked':
          appendLog({
            type: 'action_event',
            round: 0,
            action: `blocked: ${event.action}`,
            element_id: null,
            thought: event.reason,
            observation: '',
          })
          break
      }
    }

    const ws = new AgentWebSocket(sessionId, handler)
    ws.connect()
    wsRef.current = ws

    return () => {
      ws.disconnect()
      wsRef.current = null
    }
    // Store setters are stable across renders (zustand), so this effect still
    // only re-runs when sessionId changes.
  }, [sessionId, setScreenshot, setRoundNum, appendLog, appendKbDoc, setPlan, setAgentStatus, setTaskComplete, setFailureReason, setPendingConfirmation])

  // Reconciliation fallback: confirmation_required is a single WebSocket
  // message, not a retried/durable one. If it's dropped (reconnect gap,
  // packet loss), the backend sits paused with no way for the frontend to
  // find out except by asking. Poll /agent/{id}'s pending_confirmation and
  // sync it into the store whenever it disagrees with what the WS delivered
  // — reads current state via getState() instead of a reactive dependency
  // so this doesn't fight with the WS handler for the same interval.
  useEffect(() => {
    if (!sessionId) return

    const reconcile = async () => {
      try {
        const status = await agentApi.status(sessionId)
        const local = useAgentStore.getState().pendingConfirmation
        if (status.pending_confirmation && !local) {
          const raw = status.pending_confirmation
          const reconstructed: ConfirmationRequiredEvent = {
            type: 'confirmation_required',
            // Risk tier isn't persisted on AgentState.pending_confirmation
            // (see backend/agent/confirmation.py) — only the WebSocket event
            // carries it. 'medium' is the least-alarming honest default when
            // reconstructed from REST alone; the backend has already made
            // the actual pause/block decision regardless of this label.
            risk: 'medium',
            action: typeof raw.action === 'string' ? raw.action : 'unknown',
            element_id: typeof raw.element_id === 'number' ? raw.element_id : null,
            thought: typeof raw.thought === 'string' ? raw.thought : '',
          }
          setPendingConfirmation(reconstructed)
        } else if (!status.pending_confirmation && local) {
          // Resolved through some other path (another tab, the backend's
          // own confirmation timeout) — drop the stale local modal.
          setPendingConfirmation(null)
        }
      } catch {
        // Transient network failure — next poll tries again.
      }
    }

    const interval = setInterval(reconcile, RECONCILE_POLL_MS)
    return () => clearInterval(interval)
  }, [sessionId, setPendingConfirmation])

  const stop = () => wsRef.current?.send({ type: 'stop' })
  const pause = () => wsRef.current?.send({ type: 'pause' })
  const resume = () => wsRef.current?.send({ type: 'resume' })

  return { stop, pause, resume }
}
