import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { Smartphone, Database, ChevronRight } from 'lucide-react'
import { clsx } from 'clsx'
import { StatusChip } from '../components/StatusChip'
import { useAgentStore } from '../store/agentStore'
import { useDevice } from '../hooks/useDevice'
import { deviceApi, type HealthDetailed } from '../api/client'
import { getApiKey, setApiKey } from '../api/apiKey'

export function SetupPage() {
  const { deviceStatus } = useAgentStore()
  const [health, setHealth] = useState<HealthDetailed | null>(null)
  const [apiKeyInput, setApiKeyInput] = useState(getApiKey() ?? '')
  const [saved, setSaved] = useState(false)
  useDevice(4000)

  function handleSaveApiKey() {
    setApiKey(apiKeyInput.trim())
    setSaved(true)
    setTimeout(() => setSaved(false), 1500)
  }

  useEffect(() => {
    let mounted = true
    deviceApi
      .healthDetailed()
      .then((res) => {
        if (mounted) setHealth(res)
      })
      .catch(() => {
        if (mounted) setHealth(null)
      })
    return () => {
      mounted = false
    }
  }, [])

  return (
    <div className="max-w-2xl mx-auto py-12 px-4 space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-zinc-100">Setup</h1>
        <p className="text-zinc-500 text-sm mt-1">Verify your device and environment before running the agent.</p>
      </div>

      {/* Device status */}
      <div className="border border-zinc-800 rounded-xl p-5 space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Smartphone className="w-5 h-5 text-zinc-400" />
            <div>
              <div className="text-sm font-medium text-zinc-200">Android Device</div>
              <div className="text-xs text-zinc-500">Connected via ADB</div>
            </div>
          </div>
          <StatusChip status={deviceStatus} />
        </div>

        {deviceStatus?.connected && (
          <div className="grid grid-cols-2 gap-3 pt-2 border-t border-zinc-800">
            <div>
              <div className="text-xs text-zinc-500">Serial</div>
              <div className="text-sm font-mono text-zinc-300">{deviceStatus.serial}</div>
            </div>
            <div>
              <div className="text-xs text-zinc-500">Resolution</div>
              <div className="text-sm font-mono text-zinc-300">
                {deviceStatus.resolution?.[0]}×{deviceStatus.resolution?.[1]}
              </div>
            </div>
          </div>
        )}

        {!deviceStatus?.connected && (
          <div className="text-xs text-zinc-500 bg-zinc-900 rounded-lg p-3">
            Make sure USB debugging is enabled on your device and <code className="text-sky-400">adb devices</code> shows it connected.
            The backend polls every 5 seconds automatically.
          </div>
        )}
      </div>

      {/* Instructions */}
      <div className="border border-zinc-800 rounded-xl p-5 space-y-3">
        <div className="flex items-center gap-3">
          <Database className="w-5 h-5 text-zinc-400" />
          <div className="text-sm font-medium text-zinc-200">Knowledge Base</div>
        </div>
        <p className="text-xs text-zinc-500">
          The KB stores per-element documentation gathered during Explore runs. It persists across sessions via ChromaDB (cosine similarity search).
          Each app gets its own namespace — documented behaviors transfer semantically between similar apps.
        </p>
      </div>

      {/* Environment */}
      <div className="border border-zinc-800 rounded-xl p-5 space-y-3">
        <div className="text-sm font-medium text-zinc-200">Environment</div>
        <div className="space-y-1.5 text-xs font-mono">
          {[
            ['LLM_PROVIDER', 'gemini (default)'],
            ['GEMINI_API_KEY', '…set in .env…'],
            ['CHROMA_HOST', 'chromadb (docker) or localhost'],
            ['ANDROID_SERIAL', 'auto-detect or emulator-5554'],
          ].map(([k, v]) => (
            <div key={k} className="flex gap-3">
              <span className="text-sky-400 w-36 shrink-0">{k}</span>
              <span className="text-zinc-500">{v}</span>
            </div>
          ))}
        </div>
      </div>

      {/* API Key */}
      <div className="border border-zinc-800 rounded-xl p-5 space-y-3">
        <div className="text-sm font-medium text-zinc-200">API Key</div>
        <p className="text-xs text-zinc-500">
          Stored only in this browser's local storage — never baked into the app build.
          Must match the backend's <code className="text-sky-400">API_KEY</code> in <code className="text-sky-400">.env</code>.
        </p>
        <div className="flex gap-2">
          <input
            type="password"
            value={apiKeyInput}
            onChange={(e) => setApiKeyInput(e.target.value)}
            placeholder="Backend API key"
            className="flex-1 bg-zinc-900 border border-zinc-800 rounded-lg px-3 py-2 text-sm text-zinc-200 font-mono"
          />
          <button
            onClick={handleSaveApiKey}
            className="px-4 py-2 border border-zinc-700 hover:border-zinc-500 rounded-lg text-sm text-zinc-300"
          >
            {saved ? 'Saved' : 'Save'}
          </button>
        </div>
      </div>

      {/* System Status */}
      <div className="border border-zinc-800 rounded-xl p-5 space-y-4">
        <div className="text-sm font-medium text-zinc-200">System Status</div>

        {!health ? (
          <div className="flex items-center gap-3 text-xs font-mono">
            <span className={clsx('w-2 h-2 rounded-full', 'bg-red-400')} />
            <span className="text-sky-400 w-24 shrink-0">backend</span>
            <span className="text-red-400">unreachable</span>
          </div>
        ) : (
          <>
            <div className="space-y-1.5 text-xs font-mono">
              {[
                { label: 'chromadb', ok: health.chromadb.reachable, value: health.chromadb.reachable ? 'reachable' : 'unreachable' },
                { label: 'neo4j', ok: health.neo4j.reachable, value: health.neo4j.reachable ? 'reachable' : 'unreachable' },
                { label: 'langfuse', ok: health.langfuse.enabled, value: health.langfuse.enabled ? 'enabled' : 'disabled' },
                { label: 'ollama', ok: health.ollama.reachable, value: health.ollama.reachable ? 'reachable' : 'unreachable' },
              ].map((row) => (
                <div key={row.label} className="flex items-center gap-3">
                  <span className={clsx('w-2 h-2 rounded-full shrink-0', row.ok ? 'bg-emerald-400' : 'bg-red-400')} />
                  <span className="text-sky-400 w-24 shrink-0">{row.label}</span>
                  <span className={row.ok ? 'text-emerald-300' : 'text-red-400'}>{row.value}</span>
                </div>
              ))}
            </div>

            <div className="pt-2 border-t border-zinc-800">
              <div className="text-xs text-zinc-500 mb-2">Connected devices ({health.devices.count})</div>
              {health.devices.serials.length > 0 ? (
                <div className="flex flex-wrap gap-1.5">
                  {health.devices.serials.map((serial) => (
                    <span
                      key={serial}
                      className="px-2 py-0.5 bg-zinc-900 border border-zinc-800 rounded text-xs font-mono text-zinc-300"
                    >
                      {serial}
                    </span>
                  ))}
                </div>
              ) : (
                <div className="text-xs text-zinc-600">No devices connected</div>
              )}
            </div>
          </>
        )}
      </div>

      {/* Navigate */}
      <div className="flex gap-3">
        <Link
          to="/explore"
          className="flex-1 flex items-center justify-between px-4 py-3 border border-zinc-700 hover:border-zinc-500 rounded-xl text-sm text-zinc-300 hover:text-zinc-100 transition-colors"
        >
          <span>Explore Mode</span>
          <ChevronRight className="w-4 h-4" />
        </Link>
        <Link
          to="/deploy"
          className="flex-1 flex items-center justify-between px-4 py-3 border border-zinc-700 hover:border-zinc-500 rounded-xl text-sm text-zinc-300 hover:text-zinc-100 transition-colors"
        >
          <span>Deploy Mode</span>
          <ChevronRight className="w-4 h-4" />
        </Link>
      </div>
    </div>
  )
}
