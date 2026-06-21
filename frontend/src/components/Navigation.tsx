'use client'

import React, { useState } from 'react'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useModelSettings, AIModel } from '@/components/providers/ModelSettingsProvider'

interface NavigationProps {
  user?: {
    full_name?: string | null
    username?: string
  }
  onLogout?: () => void
}

type ModelOption = {
  value: AIModel
  label: string
  description: string
  provider: 'Zhipu' | 'Innospark' | 'SiliconFlow'
}

const models: ModelOption[] = [
  {
    value: 'glm-4.7',
    label: 'GLM-4.7',
    description: 'Zhipu official model',
    provider: 'Zhipu',
  },
  {
    value: 'glm-5',
    label: 'GLM-5',
    description: 'Zhipu latest generation model',
    provider: 'Zhipu',
  },
  {
    value: 'glm-5.1',
    label: 'GLM-5.1',
    description: 'Zhipu flagship model',
    provider: 'Zhipu',
  },
  {
    value: 'gpt-5.4',
    label: 'GPT-5.4',
    description: 'OpenAI model via Innospark',
    provider: 'Innospark',
  },
  {
    value: 'claude-opus-4-6',
    label: 'Claude Opus 4.6',
    description: 'Anthropic model via Innospark',
    provider: 'Innospark',
  },
  {
    value: 'claude-sonnet-4-6',
    label: 'Claude Sonnet 4.6',
    description: 'Anthropic model via Innospark',
    provider: 'Innospark',
  },
  {
    value: 'gemini-3.1-pro-preview',
    label: 'Gemini 3.1 Pro Preview',
    description: 'Google model via Innospark',
    provider: 'Innospark',
  },
  {
    value: 'gemini-3-flash-preview',
    label: 'Gemini 3 Flash Preview',
    description: 'Google fast model via Innospark',
    provider: 'Innospark',
  },
  {
    value: 'deepseek-v4-pro',
    label: 'DeepSeek V4 Pro',
    description: 'DeepSeek model via Innospark',
    provider: 'Innospark',
  },
  {
    value: 'deepseek-v4-flash',
    label: 'DeepSeek V4 Flash',
    description: 'DeepSeek fast model via Innospark',
    provider: 'Innospark',
  },
  {
    value: 'doubao-seed-2-0-pro-260215',
    label: 'Doubao Seed 2.0 Pro',
    description: 'Doubao model via Innospark',
    provider: 'Innospark',
  },
  {
    value: 'doubao-seed-2-0-code-preview-260215',
    label: 'Doubao Seed 2.0 Code',
    description: 'Doubao coding model via Innospark',
    provider: 'Innospark',
  },
  {
    value: 'kimi-k2.6',
    label: 'Kimi K2.6',
    description: 'Moonshot model via Innospark',
    provider: 'Innospark',
  },
  {
    value: 'Qwen3.6-35B-inno',
    label: 'Qwen3.6 35B Inno',
    description: 'Qwen model via Innospark',
    provider: 'Innospark',
  },
  {
    value: 'minimax-m2.5',
    label: 'MiniMax M2.5',
    description: 'SiliconFlow fallback model',
    provider: 'SiliconFlow',
  },
]

const navLinks = [
  { href: '/dashboard', label: '资源生成' },
  { href: '/ppt-upload', label: '上传 PPT' },
  { href: '/templates', label: '模板库' },
  { href: '/public_documents', label: '公开文档' },
]

function ModelGroup({
  title,
  provider,
  selectedModel,
  onSelect,
  selectedClassName,
}: {
  title: string
  provider: ModelOption['provider']
  selectedModel: AIModel
  onSelect: (model: AIModel) => void
  selectedClassName: string
}) {
  const groupModels = models.filter((model) => model.provider === provider)
  if (!groupModels.length) return null

  return (
    <>
      <div className="border-b border-t border-gray-200 bg-gray-50 px-4 py-2 text-xs font-semibold text-gray-500 first:border-t-0">
        {title}
      </div>
      {groupModels.map((model) => (
        <button
          key={model.value}
          onClick={() => onSelect(model.value)}
          className={`w-full px-4 py-3 text-left transition-colors hover:bg-gray-50 ${
            selectedModel === model.value ? selectedClassName : ''
          }`}
        >
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">
              <div className="font-semibold text-gray-900">{model.label}</div>
              <div className="mt-1 text-xs text-gray-500">{model.description}</div>
            </div>
            {selectedModel === model.value && (
              <svg className="h-5 w-5 shrink-0 text-blue-600" fill="currentColor" viewBox="0 0 20 20">
                <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
              </svg>
            )}
          </div>
        </button>
      ))}
    </>
  )
}

export default function Navigation({ user, onLogout }: NavigationProps) {
  const pathname = usePathname()
  const { selectedModel, setSelectedModel } = useModelSettings()
  const [isModelDropdownOpen, setIsModelDropdownOpen] = useState(false)

  const selectedLabel = models.find((model) => model.value === selectedModel)?.label || selectedModel

  const handleSelect = (model: AIModel) => {
    setSelectedModel(model)
    setIsModelDropdownOpen(false)
  }

  return (
    <div className="bg-white shadow">
      <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
        <div className="flex items-center justify-between py-4">
          <div className="flex items-center space-x-8">
            <h1 className="text-2xl font-bold text-gray-900">MAIC-UI</h1>
            <nav className="flex space-x-4">
              {navLinks.map((link) => (
                <Link
                  key={link.href}
                  href={link.href}
                  className={`rounded-md px-3 py-2 text-sm font-medium transition-colors ${
                    pathname === link.href
                      ? 'bg-blue-100 text-blue-700'
                      : 'text-gray-700 hover:bg-gray-100'
                  }`}
                >
                  {link.label}
                </Link>
              ))}
            </nav>
          </div>

          <div className="flex items-center space-x-4">
            <div className="relative">
              <button
                onClick={() => setIsModelDropdownOpen(!isModelDropdownOpen)}
                className="flex items-center space-x-2 rounded-md border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-50"
              >
                <span>AI 模型:</span>
                <span className="font-semibold text-blue-600">{selectedLabel}</span>
                <svg
                  className={`h-4 w-4 transition-transform ${isModelDropdownOpen ? 'rotate-180' : ''}`}
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                </svg>
              </button>

              {isModelDropdownOpen && (
                <div className="absolute right-0 z-50 mt-2 max-h-[70vh] w-80 overflow-y-auto rounded-md border border-gray-300 bg-white shadow-lg">
                  <div className="py-1">
                    <ModelGroup
                      title="Zhipu AI"
                      provider="Zhipu"
                      selectedModel={selectedModel}
                      onSelect={handleSelect}
                      selectedClassName="border-l-4 border-blue-600 bg-blue-50"
                    />
                    <ModelGroup
                      title="Innospark"
                      provider="Innospark"
                      selectedModel={selectedModel}
                      onSelect={handleSelect}
                      selectedClassName="border-l-4 border-purple-600 bg-purple-50"
                    />
                    <ModelGroup
                      title="SiliconFlow"
                      provider="SiliconFlow"
                      selectedModel={selectedModel}
                      onSelect={handleSelect}
                      selectedClassName="border-l-4 border-green-600 bg-green-50"
                    />
                  </div>
                </div>
              )}
            </div>

            {user && (
              <span className="text-sm text-gray-600">
                欢迎，{user.full_name || user.username}
              </span>
            )}
            {onLogout && (
              <button
                onClick={onLogout}
                className="rounded-md border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-50"
              >
                退出登录
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
