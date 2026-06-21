'use client'

import React, { createContext, useContext, useState, useEffect } from 'react'

export type ZhipuModel = 'glm-4.7' | 'glm-4.6' | 'glm-5' | 'glm-5.1'
export type AnthropicModel = 'claude-opus-4-6' | 'claude-sonnet-4-6'
export type OpenAICompatModel =
  | 'gpt-5.4'
  | 'deepseek-v4-pro' | 'deepseek-v4-flash' | 'deepseek-v3.2'
  | 'gemini-3.1-pro' | 'gemini-3.1-pro-preview' | 'gemini-3-flash-preview' | 'gemini-2.5-pro' | 'gemini-2.5-flash'
  | 'doubao-seed-2-0-pro-260215' | 'doubao-seed-2-0-code-preview-260215'
  | 'kimi-k2.6'
  | 'minimax-m2.5'
  | 'Qwen3.6-35B-inno'

export type LegacyAIModel =
  | 'gpt-5' | 'gpt-5.5' | 'gpt-4.1-mini'
  | 'claude-opus-4-7' | 'claude-haiku-4-5' | 'claude-opus-4-5' | 'claude-sonnet-4-5'
  | 'gemini-3.5-flash'
  | 'qwen3.6-27b' | 'qwen3.6-35b-a3b'

export type AIModel = ZhipuModel | AnthropicModel | OpenAICompatModel
export type StoredAIModel = AIModel | LegacyAIModel
export type ModelProvider = 'zhipu' | 'anthropic' | 'openai_compat'

interface ModelSettingsContextType {
  selectedModel: AIModel
  setSelectedModel: (model: StoredAIModel) => void
  getProvider: () => ModelProvider
}

const ModelSettingsContext = createContext<ModelSettingsContextType | undefined>(undefined)

function getProviderFromModel(model: StoredAIModel): ModelProvider {
  const normalized = normalizeModel(model)
  if (normalized.startsWith('glm-')) {
    return 'zhipu'
  }
  if (normalized.startsWith('claude-')) {
    return 'anthropic'
  }
  return 'openai_compat'
}

const VALID_MODELS: StoredAIModel[] = [
  'glm-4.7', 'glm-4.6', 'glm-5', 'glm-5.1',
  'claude-opus-4-6', 'claude-sonnet-4-6',
  'gpt-5.4',
  'deepseek-v4-pro', 'deepseek-v4-flash', 'deepseek-v3.2',
  'gemini-3.1-pro', 'gemini-3.1-pro-preview', 'gemini-3-flash-preview', 'gemini-2.5-pro', 'gemini-2.5-flash',
  'doubao-seed-2-0-pro-260215', 'doubao-seed-2-0-code-preview-260215',
  'kimi-k2.6',
  'minimax-m2.5',
  'Qwen3.6-35B-inno',
  'gpt-5', 'gpt-5.5', 'gpt-4.1-mini',
  'claude-opus-4-7', 'claude-haiku-4-5', 'claude-opus-4-5', 'claude-sonnet-4-5',
  'gemini-3.5-flash',
  'qwen3.6-27b', 'qwen3.6-35b-a3b',
]

function normalizeModel(model: StoredAIModel): AIModel {
  if (model === 'gemini-3.1-pro') return 'gemini-3.1-pro-preview'
  if (model === 'qwen3.6-27b' || model === 'qwen3.6-35b-a3b') return 'Qwen3.6-35B-inno'
  if (model === 'gpt-5' || model === 'gpt-5.5' || model === 'gpt-4.1-mini') return 'gpt-5.4'
  if (model === 'claude-opus-4-7' || model === 'claude-haiku-4-5' || model === 'claude-opus-4-5' || model === 'claude-sonnet-4-5') return 'claude-opus-4-6'
  if (model === 'gemini-3.5-flash') return 'gemini-3-flash-preview'
  return model
}

export function ModelSettingsProvider({ children }: { children: React.ReactNode }) {
  const [selectedModel, setSelectedModelState] = useState<AIModel>('Qwen3.6-35B-inno')

  useEffect(() => {
    const savedModel = localStorage.getItem('ai_model') as StoredAIModel | null
    const legacySavedModel = localStorage.getItem('zhipu_model') as StoredAIModel | null

    const modelToUse = savedModel || legacySavedModel
    if (modelToUse && VALID_MODELS.includes(modelToUse)) {
      const normalized = normalizeModel(modelToUse)
      setSelectedModelState(normalized)
      if (normalized !== modelToUse) {
        localStorage.setItem('ai_model', normalized)
      }
    }
  }, [])

  const setSelectedModel = (model: StoredAIModel) => {
    const normalized = normalizeModel(model)
    setSelectedModelState(normalized)
    localStorage.setItem('ai_model', normalized)
    if (normalized.startsWith('glm-')) {
      localStorage.setItem('zhipu_model', normalized)
    }
  }

  const getProvider = (): ModelProvider => {
    return getProviderFromModel(selectedModel)
  }

  const value: ModelSettingsContextType = {
    selectedModel,
    setSelectedModel,
    getProvider,
  }

  return (
    <ModelSettingsContext.Provider value={value}>
      {children}
    </ModelSettingsContext.Provider>
  )
}

export function useModelSettings() {
  const context = useContext(ModelSettingsContext)
  if (context === undefined) {
    throw new Error('useModelSettings must be used within ModelSettingsProvider')
  }
  return context
}

export { getProviderFromModel }
