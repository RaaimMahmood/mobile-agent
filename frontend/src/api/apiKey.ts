// The API key used to live only as VITE_API_KEY, baked into the JS bundle at
// build time — anyone who could load the page could read it out of the
// bundle and drive the phone. It now lives in localStorage, set once via the
// Setup page. VITE_API_KEY is kept only as the first-run default for local
// solo-dev use (backend and frontend on the same machine, same person) —
// once a real key is saved to localStorage it always wins.
const STORAGE_KEY = 'mobile-agent-api-key'

export function getApiKey(): string | undefined {
  const stored = localStorage.getItem(STORAGE_KEY)
  if (stored) return stored
  return (import.meta.env.VITE_API_KEY as string | undefined) || undefined
}

export function setApiKey(key: string): void {
  if (key) {
    localStorage.setItem(STORAGE_KEY, key)
  } else {
    localStorage.removeItem(STORAGE_KEY)
  }
}
