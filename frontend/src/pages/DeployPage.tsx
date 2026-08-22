import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { ChevronLeft, Square, CheckCircle2, Circle, ArrowRight, XCircle } from 'lucide-react'
import { clsx } from 'clsx'
import { DeviceScreen } from '../components/DeviceScreen'
import { ActionLog } from '../components/ActionLog'
import { ConfirmationModal } from '../components/ConfirmationModal'
import { RoundProgress } from '../components/RoundProgress'
import { TaskInput } from '../components/TaskInput'
import { StatusChip } from '../components/StatusChip'
import { DevicePicker } from '../components/DevicePicker'
import { useAgentStore } from '../store/agentStore'
import { useDevice } from '../hooks/useDevice'
import { useDeviceList } from '../hooks/useDeviceList'
import { useAgentStream } from '../hooks/useAgentStream'
import { agentApi, type FanoutResult } from '../api/client'

export function DeployPage() {
  const [appName, setAppName] = useState('linkedin')
  const [maxRounds, setMaxRounds] = useState('20')
  const [provider, setProvider] = useState('gemini')
  const [deviceSerial, setDeviceSerial] = useState('')
  const [maxTokens, setMaxTokens] = useState('')
  const [maxCostUsd, setMaxCostUsd] = useState('')
  const [maxLlmCalls, setMaxLlmCalls] = useState('')
  const [reasoningMode, setReasoningMode] = useState<'reasoning' | 'fast'>('fast')
  const [task, setTask] = useState('')
  const [running, setRunning] = useState(false)
  const [fanoutMode, setFanoutMode] = useState(false)
  const [selectedSerials, setSelectedSerials] = useState<string[]>([])
  const [fanoutRunning, setFanoutRunning] = useState(false)
  const [fanoutResults, setFanoutResults] = useState<FanoutResult[] | null>(null)
  const { devices, loading: devicesLoading } = useDeviceList()

  const {
    deviceStatus,
    sessionId,
    agentStatus,
    roundNum,
    taskComplete,
    failureReason,
    screenshotB64,
    screenshotRound,
    planSteps,
    currentStepIdx,
    logEntries,
    pendingConfirmation,
    setPendingConfirmation,
    setSessionId,
    resetSession,
  } = useAgentStore()

  useDevice()
  useAgentStream(sessionId)

  const buildUsage = () => ({
    ...(maxRounds.trim() ? { max_rounds: parseInt(maxRounds) } : {}),
    ...(maxTokens.trim() ? { max_tokens: parseInt(maxTokens) } : {}),
    ...(maxCostUsd.trim() ? { max_cost_usd: parseFloat(maxCostUsd) } : {}),
    ...(maxLlmCalls.trim() ? { max_llm_calls: parseInt(maxLlmCalls) } : {}),
  })

  const handleDeploy = async (submittedTask: string) => {
    if (!submittedTask.trim() || !appName.trim()) return

    if (fanoutMode) {
      if (selectedSerials.length === 0) return
      setFanoutRunning(true)
      setFanoutResults(null)
      try {
        const res = await agentApi.deployFanout({
          task: submittedTask,
          app_name: appName,
          device_serials: selectedSerials,
          provider,
          reasoning_mode: reasoningMode,
          ...buildUsage(),
        })
        setFanoutResults(res.results)
      } catch (err) {
        console.error('Failed to start fanout deploy:', err)
      } finally {
        setFanoutRunning(false)
      }
      return
    }

    setTask(submittedTask)
    setRunning(true)
    try {
      const res = await agentApi.deploy({
        task: submittedTask,
        app_name: appName,
        provider,
        reasoning_mode: reasoningMode,
        ...(deviceSerial ? { device_serial: deviceSerial } : {}),
        ...buildUsage(),
      })
      setSessionId(res.session_id)
    } catch (err) {
      console.error('Failed to start deploy:', err)
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
        <div className="flex items-center justify-between">
          <Link
            to="/setup"
            className="flex items-center gap-2 text-zinc-400 hover:text-zinc-200 text-sm transition-colors"
          >
            <ChevronLeft className="w-4 h-4" />
            Setup
          </Link>
          <h1 className="text-xl font-bold text-zinc-100">Deploy Mode</h1>
          <StatusChip status={deviceStatus} />
        </div>

        {isRunning ? (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            <div className="lg:col-span-1 space-y-4">
              <div className="border border-zinc-800 rounded-xl p-4 space-y-2">
                <div className="text-xs text-zinc-500">Task</div>
                <div className="text-sm text-zinc-200">{task}</div>
              </div>

              {planSteps.length > 0 && (
                <div className="border border-zinc-800 rounded-xl p-4 space-y-2">
                  <div className="text-xs text-zinc-500 mb-2">Plan</div>
                  {planSteps.map((step, i) => (
                    <div
                      key={i}
                      className={clsx(
                        'flex items-start gap-2 text-xs py-1',
                        i === currentStepIdx ? 'text-sky-400' : i < currentStepIdx ? 'text-zinc-500' : 'text-zinc-600',
                      )}
                    >
                      {i < currentStepIdx ? (
                        <CheckCircle2 className="w-3.5 h-3.5 shrink-0 mt-0.5" />
                      ) : i === currentStepIdx ? (
                        <ArrowRight className="w-3.5 h-3.5 shrink-0 mt-0.5" />
                      ) : (
                        <Circle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
                      )}
                      <span className={i < currentStepIdx ? 'line-through' : ''}>{step}</span>
                    </div>
                  ))}
                </div>
              )}

              <div className="border border-zinc-800 rounded-xl p-4">
                <RoundProgress current={roundNum} max={parseInt(maxRounds)} status={agentStatus} />
              </div>

              {taskComplete && (
                <div className="flex items-center gap-2 px-3 py-2 bg-emerald-950 border border-emerald-800 rounded-lg text-emerald-300 text-xs">
                  <CheckCircle2 className="w-4 h-4" />
                  Task completed
                </div>
              )}

              {failureReason && (
                <div className="px-3 py-2 bg-red-950 border border-red-800 rounded-lg text-red-300 text-xs">
                  {failureReason}
                </div>
              )}

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
          <div className="max-w-xl mx-auto space-y-4">
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
              </div>

              <div>
                <label className="block text-xs text-zinc-500 mb-2">Device</label>
                <div className="grid grid-cols-2 gap-2 mb-2">
                  <button
                    type="button"
                    onClick={() => {
                      setFanoutMode(false)
                      setFanoutResults(null)
                    }}
                    className={clsx(
                      'px-3 py-1.5 text-xs rounded-lg border transition-colors',
                      !fanoutMode
                        ? 'bg-zinc-800 border-zinc-600 text-zinc-100'
                        : 'bg-zinc-900 border-zinc-700 text-zinc-500 hover:text-zinc-300',
                    )}
                  >
                    Single device
                  </button>
                  <button
                    type="button"
                    onClick={() => setFanoutMode(true)}
                    className={clsx(
                      'px-3 py-1.5 text-xs rounded-lg border transition-colors',
                      fanoutMode
                        ? 'bg-zinc-800 border-zinc-600 text-zinc-100'
                        : 'bg-zinc-900 border-zinc-700 text-zinc-500 hover:text-zinc-300',
                    )}
                  >
                    Multiple devices
                  </button>
                </div>

                {fanoutMode ? (
                  <div className="space-y-1.5 border border-zinc-800 rounded-lg p-3 max-h-40 overflow-y-auto">
                    {devicesLoading && devices.length === 0 ? (
                      <div className="text-xs text-zinc-600">Loading devices…</div>
                    ) : devices.length === 0 ? (
                      <div className="text-xs text-zinc-600">No devices found</div>
                    ) : (
                      devices.map((d) => {
                        const checked = selectedSerials.includes(d.serial)
                        return (
                          <label
                            key={d.serial}
                            className="flex items-center gap-2 text-sm text-zinc-300 cursor-pointer"
                          >
                            <input
                              type="checkbox"
                              checked={checked}
                              onChange={() =>
                                setSelectedSerials((prev) =>
                                  checked ? prev.filter((s) => s !== d.serial) : [...prev, d.serial],
                                )
                              }
                              className="accent-sky-400"
                            />
                            <span className="font-mono text-xs">{d.serial}</span>
                            {d.busy && <span className="text-[10px] text-amber-400">(busy)</span>}
                          </label>
                        )
                      })
                    )}
                  </div>
                ) : (
                  <DevicePicker value={deviceSerial} onChange={setDeviceSerial} />
                )}
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

              <div>
                <label className="block text-xs text-zinc-500 mb-2">Reasoning Mode</label>
                <select
                  value={reasoningMode}
                  onChange={(e) => setReasoningMode(e.target.value as 'reasoning' | 'fast')}
                  className="w-full bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-zinc-200 focus:outline-none focus:border-zinc-500"
                >
                  <option value="reasoning">Reasoning (vision every round)</option>
                  <option value="fast">Fast (text-first, escalates when needed)</option>
                </select>
              </div>

              <div>
                <label className="block text-xs text-zinc-500 mb-2">Task</label>
                <TaskInput onSubmit={handleDeploy} disabled={!deviceStatus?.connected || fanoutRunning} />
              </div>

              <p className="text-xs text-zinc-500 bg-zinc-900 rounded-lg p-3">
                Deploy mode decomposes your task into ordered sub-steps, then executes them using the knowledge base
                built during Explore runs. Progress is re-checked every 5 rounds.
              </p>
            </div>

            {fanoutMode && fanoutResults && (
              <div className="border border-zinc-800 rounded-xl p-5 space-y-3">
                <div className="text-sm font-medium text-zinc-200">Fan-out Results</div>
                <div className="overflow-x-auto">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="text-left text-zinc-500 border-b border-zinc-800">
                        <th className="py-2 pr-4 font-medium">Device Serial</th>
                        <th className="py-2 pr-4 font-medium">Started</th>
                        <th className="py-2 font-medium">Session</th>
                      </tr>
                    </thead>
                    <tbody>
                      {fanoutResults.map((r) => (
                        <tr key={r.device_serial} className="border-b border-zinc-800 last:border-0">
                          <td className="py-2 pr-4 font-mono text-zinc-300">{r.device_serial}</td>
                          <td className="py-2 pr-4">
                            {r.started ? (
                              <CheckCircle2 className="w-4 h-4 text-emerald-400" />
                            ) : (
                              <XCircle className="w-4 h-4 text-red-400" />
                            )}
                          </td>
                          <td className="py-2">
                            {r.started && r.session_id ? (
                              <Link
                                to={`/history/${r.session_id}`}
                                className="text-sky-400 hover:text-sky-300 font-mono truncate block max-w-56"
                                title={r.session_id}
                              >
                                {r.session_id}
                              </Link>
                            ) : (
                              <span className="text-zinc-500">{r.detail ?? 'Not started'}</span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
