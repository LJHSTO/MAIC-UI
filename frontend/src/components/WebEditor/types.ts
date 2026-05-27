export interface DocumentVersion {
	id: string
	documentId: number
	versionNumber?: number
	name: string
	modifiedDate: string
	modificationPrompt: string
	html: string
	isCurrent: boolean
	isRoot?: boolean
}

export interface WebEditorProps {
	targetIframeRef?: React.RefObject<HTMLIFrameElement>
	documentId?: number
	isPublic?: boolean
	resourceType?: 'web' | 'ppt'
	slideNumber?: number
	onEditStatusChange?: (status: 'idle' | 'processing' | 'completed') => void
	onEditModeChange?: (isEditMode: boolean) => void
	onVersionSaved?: (version?: DocumentVersion) => void
}

export type EditStatus = 'idle' | 'processing' | 'completed'
export type VersionType = 'before' | 'after'
export type ModelType = 'claude-sonnet-4-6' | 'claude-opus-4-6' | 'claude-opus-4-5' | 'claude-sonnet-4-5' | 'claude-haiku-4-5' | 'glm-4.7' | 'glm-4.6'
  | 'gpt-5' | 'gpt-5.4' | 'gpt-5.5' | 'gpt-4.1-mini'
  | 'deepseek-v4-pro' | 'deepseek-v4-flash'
  | 'gemini-3.1-pro' | 'gemini-3.5-flash' | 'gemini-3-flash-preview' | 'gemini-2.5-pro'
  | 'kimi-k2.6'
  | 'glm-5.1' | 'minimax-m2.5' | 'qwen3.6-35b-a3b'

export const MODEL_OPTIONS: ReadonlyArray<{ label: string; value: ModelType }> = [
	{ label: 'GLM 4.7', value: 'glm-4.7' },
	{ label: 'GLM 4.6', value: 'glm-4.6' },
	{ label: 'Claude Sonnet 4.6', value: 'claude-sonnet-4-6' },
	{ label: 'Claude Opus 4.6', value: 'claude-opus-4-6' },
	{ label: 'Claude Haiku 4.5', value: 'claude-haiku-4-5' },
	{ label: 'Claude Opus 4.5', value: 'claude-opus-4-5' },
	{ label: 'Claude Sonnet 4.5', value: 'claude-sonnet-4-5' },
	{ label: 'GPT-5', value: 'gpt-5' },
	{ label: 'GPT-5.4', value: 'gpt-5.4' },
  { label: 'GPT-5.5', value: 'gpt-5.5' },
	{ label: 'GPT-4.1 Mini', value: 'gpt-4.1-mini' },
	{ label: 'DeepSeek V4 Pro', value: 'deepseek-v4-pro' },
	{ label: 'DeepSeek V4 Flash', value: 'deepseek-v4-flash' },
  { label: 'Gemini 3.1 Pro', value: 'gemini-3.1-pro' },
  { label: 'Gemini 3.5 Flash', value: 'gemini-3.5-flash' },
	{ label: 'Gemini 3 Flash', value: 'gemini-3-flash-preview' },
	{ label: 'Gemini 2.5 Pro', value: 'gemini-2.5-pro' },
  { label: 'Kimi K2.6', value: 'kimi-k2.6' },
  { label: 'GLM 5.1', value: 'glm-5.1' },
  { label: 'MiniMax M2.5', value: 'minimax-m2.5' },
  { label: 'Qwen3.6 35B A3B', value: 'qwen3.6-35b-a3b' },
]

export const MODEL_LABEL_TO_TYPE: Record<string, ModelType> = {
	'Claude Sonnet 4.6': 'claude-sonnet-4-6',
	'Claude Opus 4.6': 'claude-opus-4-6',
	'Claude Haiku 4.5': 'claude-haiku-4-5',
	'Claude Opus 4.5': 'claude-opus-4-5',
	'Claude Sonnet 4.5': 'claude-sonnet-4-5',
	'GLM 4.7': 'glm-4.7',
	'GLM 4.6': 'glm-4.6',
	'GPT-5': 'gpt-5',
	'GPT-5.4': 'gpt-5.4',
  'GPT-5.5': 'gpt-5.5',
	'GPT-4.1 Mini': 'gpt-4.1-mini',
	'DeepSeek V4 Pro': 'deepseek-v4-pro',
	'DeepSeek V4 Flash': 'deepseek-v4-flash',
  'Gemini 3.1 Pro': 'gemini-3.1-pro',
  'Gemini 3.5 Flash': 'gemini-3.5-flash',
	'Gemini 3 Flash': 'gemini-3-flash-preview',
	'Gemini 2.5 Pro': 'gemini-2.5-pro',
  'Kimi K2.6': 'kimi-k2.6',
  'GLM 5.1': 'glm-5.1',
  'MiniMax M2.5': 'minimax-m2.5',
  'Qwen3.6 35B A3B': 'qwen3.6-35b-a3b',
}

export const MODEL_TYPE_TO_LABEL: Record<ModelType, string> = {
	'claude-sonnet-4-6': 'Claude Sonnet 4.6',
	'claude-opus-4-6': 'Claude Opus 4.6',
	'claude-haiku-4-5': 'Claude Haiku 4.5',
	'claude-opus-4-5': 'Claude Opus 4.5',
	'claude-sonnet-4-5': 'Claude Sonnet 4.5',
	'glm-4.7': 'GLM 4.7',
	'glm-4.6': 'GLM 4.6',
	'gpt-5': 'GPT-5',
	'gpt-5.4': 'GPT-5.4',
  'gpt-5.5': 'GPT-5.5',
	'gpt-4.1-mini': 'GPT-4.1 Mini',
	'deepseek-v4-pro': 'DeepSeek V4 Pro',
	'deepseek-v4-flash': 'DeepSeek V4 Flash',
  'gemini-3.1-pro': 'Gemini 3.1 Pro',
  'gemini-3.5-flash': 'Gemini 3.5 Flash',
	'gemini-3-flash-preview': 'Gemini 3 Flash',
	'gemini-2.5-pro': 'Gemini 2.5 Pro',
  'kimi-k2.6': 'Kimi K2.6',
  'glm-5.1': 'GLM 5.1',
  'minimax-m2.5': 'MiniMax M2.5',
  'qwen3.6-35b-a3b': 'Qwen3.6 35B A3B',
}

export interface HighlightContext {
	doc: Document
	iframe: HTMLIFrameElement | null
}

export interface CitationListItem {
	id: number
	index: number
	html: string
	selector: string
	note?: string
	pageBadge?: HTMLDivElement
	highlightOverlay?: HTMLDivElement
	element?: Element
	context?: HighlightContext
}

export type CitationListNode = {
	value: CitationListItem
	next: CitationListNode | null
}

export interface EditorConfig {
	buttonPosition: string
	highlightColor: string
	copyNotification: boolean
	disableAnimations: boolean
	excludeSelectors: string[]
}

export interface WebEditRequest {
	document_id: number
	slide_number?: number
	citations: Array<{
		id: number
		index: number
		html: string
		selector: string
		note?: string
	}>
	user_prompt: string
	thinking_enabled?: boolean
	model?: ModelType
}

export interface WebEditStatusResponse {
	status: 'not_started' | 'processing' | 'ready' | 'error'
	modified_html?: string
	error?: string
}

export interface WebEditResponse {
	status: 'processing' | 'success'
}
