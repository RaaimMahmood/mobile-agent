import { agentApi } from '../api/client'
import type { ConfirmationRequiredEvent } from '../api/websocket'

interface Props {
  sessionId: string
  event: ConfirmationRequiredEvent
  onResolved: () => void
}

export function ConfirmationModal({ sessionId, event, onResolved }: Props) {
  async function respond(approve: boolean) {
    await agentApi.confirm(sessionId, approve)
    onResolved()
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
        <div className="flex gap-3 justify-end">
          <button
            onClick={() => respond(false)}
            className="px-4 py-2 rounded-lg text-sm border border-zinc-700 text-zinc-300 hover:border-zinc-500"
          >
            Reject
          </button>
          <button
            onClick={() => respond(true)}
            className="px-4 py-2 rounded-lg text-sm bg-sky-600 hover:bg-sky-500 text-white"
          >
            Approve
          </button>
        </div>
      </div>
    </div>
  )
}
