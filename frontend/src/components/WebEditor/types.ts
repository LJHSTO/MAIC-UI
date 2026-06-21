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
export type ModelType =
	| 'claude-sonnet-4-6' | 'claude-opus-4-6'
	| 'glm-4.7' | 'glm-4.6' | 'glm-5' | 'glm-5.1'
	| 'gpt-5.4'
	| 'deepseek-v4-pro' | 'deepseek-v4-flash'
	| 'gemini-3.1-pro-preview' | 'gemini-3-flash-preview' | 'gemini-2.5-pro'
	| 'doubao-seed-2-0-pro-260215' | 'doubao-seed-2-0-code-preview-260215'
	| 'kimi-k2.6'
	| 'minimax-m2.5' | 'Qwen3.6-35B-inno'

export const MODEL_OPTIONS: ReadonlyArray<{ label: string; value: ModelType }> = [
	{ label: 'GLM 4.7', value: 'glm-4.7' },
	{ label: 'GLM 4.6', value: 'glm-4.6' },
	{ label: 'GLM 5', value: 'glm-5' },
	{ label: 'GLM 5.1', value: 'glm-5.1' },
	{ label: 'Claude Sonnet 4.6', value: 'claude-sonnet-4-6' },
	{ label: 'Claude Opus 4.6', value: 'claude-opus-4-6' },
	{ label: 'GPT-5.4', value: 'gpt-5.4' },
	{ label: 'DeepSeek V4 Pro', value: 'deepseek-v4-pro' },
	{ label: 'DeepSeek V4 Flash', value: 'deepseek-v4-flash' },
	{ label: 'Gemini 3.1 Pro Preview', value: 'gemini-3.1-pro-preview' },
	{ label: 'Gemini 3 Flash', value: 'gemini-3-flash-preview' },
	{ label: 'Gemini 2.5 Pro', value: 'gemini-2.5-pro' },
	{ label: 'Doubao Seed 2.0 Pro', value: 'doubao-seed-2-0-pro-260215' },
	{ label: 'Doubao Seed 2.0 Code', value: 'doubao-seed-2-0-code-preview-260215' },
	{ label: 'Kimi K2.6', value: 'kimi-k2.6' },
	{ label: 'MiniMax M2.5', value: 'minimax-m2.5' },
	{ label: 'Qwen3.6 35B Inno', value: 'Qwen3.6-35B-inno' },
]

export const MODEL_LABEL_TO_TYPE: Record<string, ModelType> = Object.fromEntries(
	MODEL_OPTIONS.map((option) => [option.label, option.value])
) as Record<string, ModelType>

export const MODEL_TYPE_TO_LABEL: Record<ModelType, string> = Object.fromEntries(
	MODEL_OPTIONS.map((option) => [option.value, option.label])
) as Record<ModelType, string>

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
