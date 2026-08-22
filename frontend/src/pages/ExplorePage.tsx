import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { ChevronLeft, Square } from 'lucide-react'
import { DeviceScreen } from '../components/DeviceScreen'
import { ActionLog } from '../components/ActionLog'
import { ConfirmationModal } from '../components/ConfirmationModal'
import { RoundProgress } from '../components/RoundProgress'
import { TaskInput } from '../components/TaskInput'
import { StatusChip } from '../components/StatusChip'
import { DevicePicker } from '../components/DevicePicker'
import { useAgentStore } from '../store/agentStore'
import { useDevice } from '../hooks/useDevice'
import { useAgentStream } from '../hooks/useAgentStream'
import { agentApi } from '../api/client'

export function ExplorePage() {
  const [appName, setAppName] = useState('linkedin')
  const [maxRounds, setMaxRounds] = useState('10')
  const [provider, setProvider] = useState('gemini')
  const [deviceSerial, setDeviceSerial] = useState('')
  const [maxTokens, setMaxTokens] = useState('')
  const [maxCostUsd, setMaxCostUsd] = useState('')
  const [maxLlmCalls, setMaxLlmCalls] = useState('')
  const [running, setRunning] = useState(false)

  const {
    deviceStatus,
    sessionId,
    agentStatus,
    roundNum,
    screenshotB64,
    screenshotRound,
    logEntries,
    kbDocs,
    pendingConfirmation,
    setPendingConfirmation,
    setSessionId,
    resetSession,
  } = useAgentStore()

  useDevice()
  useAgentStream(sessionId)

  const handleExplore = async () => {
    if (!appName.trim()) return
    setRunning(true)
    try {
      const res = await agentApi.explore({
        app_name: appName,
        provider,
        ...(maxRounds.trim() ? { max_rounds: parseInt(maxRounds) } : {}),
        ...(deviceSerial ? { device_serial: deviceSerial } : {}),
        ...(maxTokens.trim() ? { max_tokens: parseInt(maxTokens) } : {}),
        ...(maxCostUsd.trim() ? { max_cost_usd: parseFloat(maxCostUsd) } : {}),
        ...(maxLlmCalls.trim() ? { max_llm_calls: parseInt(maxLlmCalls) } : {}),
      })
      setSessionId(res.session_id)
    } catch (err) {
      console.error('Failed to start explore:', err)
      setRunning(false)
    }
  }

  const handleStop = async () => {
    if (sessionId) {
      await agentApi.stop(sessionId)
      resetSession()
      setRunning(false)
    }
  }

  useEffect(() => {
    if (agentStatus === 'done' || agentStatus === 'error') {
      setRunning(false)
    }
  }, [agentStatus])

  const isRunning = running || agentStatus === 'running'

  return (
    <div className="min-h-screen bg-zinc-950 py-6 px-4">
      {sessionId && pendingConfirmation && (
        <ConfirmationModal
          sessionId={sessionId}
          event={pendingConfirmation}
          onResolved={() => setPendingConfirmation(null)}
        />
      )}
      <div className="max-w-7xl mx-auto space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <Link
            to="/setup"
            className="flex items-center gap-2 text-zinc-400 hover:text-zinc-200 text-sm transition-colors"
          >
            <ChevronLeft className="w-4 h-4" />
            Setup
          </Link>
          <h1 className="text-xl font-bold text-zinc-100">Explore Mode</h1>
          <StatusChip status={deviceStatus} />
        </div>

        {isRunning ? (
          // Live run view
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            <div className="lg:col-span-1 space-y-4">
              <div className="border border-zinc-800 rounded-xl p-4 space-y-3">
                <div>
                  <div className="text-xs text-zinc-500 mb-2">App</div>
                  <div className="text-sm font-mono text-zinc-300">{appName}</div>
                </div>
                <div>
                  <div className="text-xs text-zinc-500 mb-2">Provider</div>
                  <div className="text-sm font-mono text-zinc-300">{provider}</div>
                </div>
                <div>
                  <div className="text-xs text-zinc-500 mb-2">KB Documents</div>
                  <div className="text-sm font-mono text-sky-400">{kbDocs.length}</div>
                </div>
              </div>

              <div className="border border-zinc-800 rounded-xl p-4">
                <RoundProgress
                  current={roundNum}
                  max={parseInt(maxRounds)}
                  status={agentStatus}
                />
              </div>

              <button
                onClick={handleStop}
                className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-red-950 hover:bg-red-900 text-red-300 text-sm rounded-lg transition-colors"
              >
                <Square className="w-4 h-4" />
                Stop
              </button>
            </div>

            <div className="lg:col-span-2 space-y-4">
              <div className="border border-zinc-800 rounded-xl overflow-hidden bg-zinc-900/50">
                <DeviceScreen screenshotB64={screenshotB64} round={screenshotRound} />
              </div>
              <div className="border border-zinc-800 rounded-xl overflow-hidden bg-zinc-900/50">
                <ActionLog entries={logEntries} height={240} />
              </div>
            </div>
          </div>
        ) : (
          // Config view
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <div className="space-y-4">
              <div className="border border-zinc-800 rounded-xl p-5 space-y-4">
                <div>
                  <label className="block text-xs text-zinc-500 mb-2">App Name</label>
                  <input
                    type="text"
                    value={appName}
                    onChange={(e) => setAppName(e.target.value)}
                    placeholder="e.g. linkedin, twitter, gmail"
                    className="w-full bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-zinc-200 placeholder-zinc-600 focus:outline-none focus:border-zinc-500"
                  />
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-xs text-zinc-500 mb-2">Max Rounds</label>
                    <input
                      type="number"
                      value={maxRounds}
                      onChange={(e) => setMaxRounds(e.target.value)}
                      min="1"
                      max="100"
                      className="w-full bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-zinc-200 focus:outline-none focus:border-zinc-500"
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-zinc-500 mb-2">Device</label>
                    <DevicePicker value={deviceSerial} onChange={setDeviceSerial} />
                  </div>
                </div>

                <div>
                  <label className="block text-xs text-zinc-500 mb-2">LLM Provider</label>
                  <select
                    value={provider}
                    onChange={(e) => setProvider(e.target.value)}
                    className="w-full bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-zinc-200 focus:outline-none focus:border-zinc-500"
                  >
                    <option value="gemini">Gemini 2.5 Flash</option>
                    <option value="openai">GPT-4o</option>
                    <option value="anthropic">Claude</option>
                    <option value="ollama">Ollama (local)</option>
                    <option value="cerebras">Cerebras (text-only)</option>
                    <option value="glm">GLM</option>
                  </select>
                </div>

                <div className="grid grid-cols-3 gap-3">
                  <div>
                    <label className="block text-xs text-zinc-500 mb-2">Max Tokens</label>
                    <input
                      type="number"
                      value={maxTokens}
                      onChange={(e) => setMaxTokens(e.target.value)}
                      min="0"
                      className="w-full bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-zinc-200 focus:outline-none focus:border-zinc-500"
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-zinc-500 mb-2">Max Cost ($)</label>
                    <input
                      type="number"
                      value={maxCostUsd}
                      onChange={(e) => setMaxCostUsd(e.target.value)}
                      min="0"
                      step="0.01"
                      className="w-full bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-zinc-200 focus:outline-none focus:border-zinc-500"
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-zinc-500 mb-2">Max LLM Calls</label>
                    <input
                      type="number"
                      value={maxLlmCalls}
                      onChange={(e) => setMaxLlmCalls(e.target.value)}
                      min="0"
                      className="w-full bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-zinc-200 focus:outline-none focus:border-zinc-500"
                    />
                  </div>
                </div>

                <TaskInput
                  onSubmit={() => handleExplore()}
                  disabled={!deviceStatus?.connected}
                  placeholder="Press Run to start exploring…"
                  suggestions={[
                    'Explore LinkedIn UI',
                    'Explore Gmail interface',
                    'Explore Twitter features',
                    'Explore Maps navigation',
                  ]}
                />

                <p className="text-xs text-zinc-500 bg-zinc-900 rounded-lg p-3">
                  Explore mode systematically taps all interactive elements on screen, annotates them, and builds a knowledge base of their behaviors.
                  The agent learns what each UI element does without guidance.
                </p>
              </div>
            </div>

            <div className="space-y-4">
              <div className="border border-zinc-800 rounded-xl p-5">
                <div className="text-sm font-medium text-zinc-200 mb-4">Previous Runs</div>
                {kbDocs.length === 0 ? (
                  <div className="text-xs text-zinc-500 text-center py-8">No explore runs yet</div>
                ) : (
                  <div className="space-y-2">
                    {kbDocs.slice(0, 5).map((doc) => (
                      <div key={doc.id} className="text-xs border border-zinc-800 rounded p-2 text-zinc-400">
                        <div className="font-mono truncate">{doc.element_sig}</div>
                        <div className="text-zinc-600 mt-1 line-clamp-2">{doc.documentation}</div>
                      </div>
                    ))}
                    {kbDocs.length > 5 && (
                      <div className="text-xs text-zinc-500 pt-2 border-t border-zinc-800">
                        … and {kbDocs.length - 5} more
                      </div>
                    )}
                  </div>
                )}
              </div>

              <Link
                to="/kb"
                className="block text-center px-4 py-2 border border-zinc-700 hover:border-zinc-500 text-zinc-400 hover:text-zinc-200 text-sm rounded-lg transition-colors"
              >
                View Full Knowledge Base
              </Link>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
