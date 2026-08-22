import { useState } from 'react'
import axios from 'axios'
import { agentApi } from '../api/client'
import type { ConfirmationRequiredEvent } from '../api/websocket'

interface Props {
  sessionId: string
  event: ConfirmationRequiredEvent
  onResolved: () => void
}

export function ConfirmationModal({ sessionId, event, onResolved }: Props) {
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Remembers the last attempted choice so Retry can resend the same
  // approve/reject without the user re-clicking the original button.
  const [lastChoice, setLastChoice] = useState<boolean | null>(null)

  async function respond(approve: boolean) {
    setLastChoice(approve)
    setError(null)
    setIsSubmitting(true)
    try {
      await agentApi.confirm(sessionId, approve)
      onResolved()
    } catch (err) {
      // 409 means the backend already resolved this (e.g. the 5-minute
      // confirmation timeout fired, or another tab responded first) — there
      // is nothing left to confirm, so just close rather than show an error
      // for a choice that no longer matters.
      if (axios.isAxiosError(err) && err.response?.status === 409) {
        onResolved()
        return
      }
      // Any other failure (network drop, 5xx): re-check with the backend
      // instead of assuming our own guess about what state it's in — the
      // action may since have resolved by some other path.
      try {
        const status = await agentApi.status(sessionId)
        if (!status.pending_confirmation) {
          onResolved()
          return
        }
      } catch {
        // Status check itself failed too — fall through to showing the
        // original error with a retry option.
      }
      const message = err instanceof Error ? err.message : 'Failed to send response'
      setError(message)
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
      <div className="bg-zinc-900 border border-zinc-700 rounded-xl p-6 max-w-md w-full space-y-4">
        <div className="flex items-center gap-2">
          <span
            className={
              event.risk === 'high'
                ? 'px-2 py-0.5 rounded text-xs font-mono bg-red-900/40 text-red-300'
                : 'px-2 py-0.5 rounded text-xs font-mono bg-amber-900/40 text-amber-300'
            }
          >
            {event.risk.toUpperCase()} RISK
          </span>
          <h2 className="text-sm font-medium text-zinc-100">Confirm action</h2>
        </div>
        <div className="text-sm text-zinc-300">
          <div>
            Action: <span className="font-mono text-zinc-100">{event.action}</span>
            {event.element_id !== null && (
              <span className="text-zinc-500"> on element #{event.element_id}</span>
            )}
          </div>
          {event.thought && <div className="text-zinc-500 mt-1">{event.thought}</div>}
        </div>

        {error && (
          <div className="px-3 py-2 bg-red-950 border border-red-800 rounded-lg text-red-300 text-xs space-y-2">
            <div>{error}</div>
            <div className="flex gap-2">
              <button
                onClick={() => lastChoice !== null && respond(lastChoice)}
                disabled={isSubmitting}
                className="px-2 py-1 rounded border border-red-800 hover:border-red-600 disabled:opacity-50"
              >
                Retry
              </button>
              <button
                onClick={onResolved}
                className="px-2 py-1 rounded border border-zinc-700 hover:border-zinc-500 text-zinc-300"
              >
                Dismiss
              </button>
            </div>
          </div>
        )}

        <div className="flex gap-3 justify-end">
          <button
            onClick={() => respond(false)}
            disabled={isSubmitting}
            className="px-4 py-2 rounded-lg text-sm border border-zinc-700 text-zinc-300 hover:border-zinc-500 disabled:opacity-50"
          >
            Reject
          </button>
          <button
            onClick={() => respond(true)}
            disabled={isSubmitting}
            className="px-4 py-2 rounded-lg text-sm bg-sky-600 hover:bg-sky-500 text-white disabled:opacity-50"
          >
            {isSubmitting ? 'Sending…' : 'Approve'}
          </button>
        </div>
      </div>
    </div>
  )
}
