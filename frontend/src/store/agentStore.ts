import { create } from 'zustand'
import type { ActionEvent, KBUpdateEvent, ConfirmationRequiredEvent } from '../api/websocket'
import type { DeviceStatus, KBDoc } from '../api/client'

export interface LogEntry {
  round: number
  action: string
  element_id: number | null
  thought: string
  observation: string
  timestamp: number
}

interface AgentStore {
  // Device
  deviceStatus: DeviceStatus | null
  setDeviceStatus: (s: DeviceStatus) => void

  // Agent session
  sessionId: string | null
  setSessionId: (id: string | null) => void
  agentStatus: string
  setAgentStatus: (s: string) => void
  roundNum: number
  setRoundNum: (n: number) => void
  taskComplete: boolean
  setTaskComplete: (v: boolean) => void
  failureReason: string | null
  setFailureReason: (r: string | null) => void

  // Screenshot
  screenshotB64: string
  screenshotRound: number
  setScreenshot: (b64: string, round: number) => void

  // Plan
  planSteps: string[]
  currentStepIdx: number
  setPlan: (steps: string[]) => void

  // Action log
  logEntries: LogEntry[]
  appendLog: (e: ActionEvent) => void
  clearLog: () => void

  // KB
  kbDocs: KBDoc[]
  appendKbDoc: (doc: KBUpdateEvent['doc']) => void
  setKbDocs: (docs: KBDoc[]) => void

  // Confirmation gate
  pendingConfirmation: ConfirmationRequiredEvent | null
  setPendingConfirmation: (e: ConfirmationRequiredEvent | null) => void

  // Reset for new session
  resetSession: () => void
}

export const useAgentStore = create<AgentStore>((set) => ({
  deviceStatus: null,
  setDeviceStatus: (s) => set({ deviceStatus: s }),

  sessionId: null,
  setSessionId: (id) => set({ sessionId: id }),
  agentStatus: 'idle',
  setAgentStatus: (s) => set({ agentStatus: s }),
  roundNum: 0,
  setRoundNum: (n) => set({ roundNum: n }),
  taskComplete: false,
  setTaskComplete: (v) => set({ taskComplete: v }),
  failureReason: null,
  setFailureReason: (r) => set({ failureReason: r }),

  screenshotB64: '',
  screenshotRound: 0,
  setScreenshot: (b64, round) => set({ screenshotB64: b64, screenshotRound: round }),

  planSteps: [],
  currentStepIdx: 0,
  setPlan: (steps) => set({ planSteps: steps, currentStepIdx: 0 }),

  logEntries: [],
  appendLog: (e) =>
    set((state) => ({
      logEntries: [
        ...state.logEntries,
        {
          round: e.round,
          action: e.action,
          element_id: e.element_id,
          thought: e.thought,
          observation: e.observation,
          timestamp: Date.now(),
        },
      ],
    })),
  clearLog: () => set({ logEntries: [] }),

  kbDocs: [],
  appendKbDoc: (doc) =>
    set((state) => {
      const existing = state.kbDocs.findIndex((d) => d.id === doc.id)
      if (existing >= 0) {
        const updated = [...state.kbDocs]
        updated[existing] = { ...updated[existing], documentation: doc.documentation }
        return { kbDocs: updated }
      }
      return {
        kbDocs: [
          ...state.kbDocs,
          {
            id: doc.id,
            app_name: doc.app_name,
            element_sig: doc.element_sig,
            class_name: '',
            resource_id: '',
            content_desc: '',
            text: '',
            documentation: doc.documentation,
            observed_result: '',
            last_explored_at: new Date().toISOString(),
          },
        ],
      }
    }),
  setKbDocs: (docs) => set({ kbDocs: docs }),

  pendingConfirmation: null,
  setPendingConfirmation: (e) => set({ pendingConfirmation: e }),

  resetSession: () =>
    set({
      sessionId: null,
      agentStatus: 'idle',
      roundNum: 0,
      taskComplete: false,
      failureReason: null,
      screenshotB64: '',
      screenshotRound: 0,
      planSteps: [],
      currentStepIdx: 0,
      logEntries: [],
      pendingConfirmation: null,
    }),
}))
