import createClient from 'openapi-fetch'
import type { paths } from './types'

// Single shared instance; baseUrl '/' lets the Vite proxy (/api → :8000) handle routing in dev
// and FastAPI serve the built SPA directly in production.
export const apiClient = createClient<paths>({ baseUrl: '/' })
