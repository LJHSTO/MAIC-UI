'use client'

import React, { useCallback, useRef, useState } from 'react'
import { Button } from '@/components/ui/Button'
import { useModelSettings } from '@/components/providers/ModelSettingsProvider'
import { useLanguage } from '@/components/providers/LanguageProvider'
import { ConceptInputForm } from './ConceptInputForm'
import PPTUploadForm from '@/components/ppt-viewer/PPTUploadForm'
import { getStoredAuthToken } from '@/lib/auth-token'

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL ||
  (process.env.NODE_ENV === 'production' ? '/api' : 'http://localhost:8000/api')

interface UploadResponse {
  id: number
  original_filename: string
  status: string
}

interface FormDataState {
  title: string
  subject: string
  concept_name: string
  concept_overview: string
  mastery_points: string
  design_idea: string
  grade_level: string
  description: string
  interests: string
}

interface PDFUploadFormProps {
  hideTypeSelector?: boolean
  initialType?: 'pdf' | 'ppt' | 'concept'
}

export function PDFUploadForm({ hideTypeSelector = false, initialType = 'pdf' }: PDFUploadFormProps = {}) {
  const { selectedModel } = useModelSettings()
  const { t, language } = useLanguage()
  const fileInputRef = useRef<HTMLInputElement | null>(null)

  const [inputType, setInputType] = useState<'pdf' | 'ppt' | 'concept'>(initialType)
  const [file, setFile] = useState<File | null>(null)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [dragActive, setDragActive] = useState(false)

  const [formData, setFormData] = useState<FormDataState>({
    title: '',
    subject: '',
    concept_name: '',
    concept_overview: '',
    mastery_points: '',
    design_idea: '',
    grade_level: '',
    description: '',
    interests: ''
  })

  const [autoNavigate, setAutoNavigate] = useState(true)
  const [isPublic, setIsPublic] = useState(false)
  const [includeExercises, setIncludeExercises] = useState(false)
  const [includePrerequisites, setIncludePrerequisites] = useState(false)
  const [generationMode, setGenerationMode] = useState<'fast' | 'heavy'>('heavy')

  const getAuthToken = () => getStoredAuthToken()

  const applySelectedFile = useCallback(
    (selectedFile: File) => {
      if (selectedFile.type !== 'application/pdf') {
        setError(t('upload.error_pdf_only'))
        return
      }
      setFile(selectedFile)
      setError(null)
      if (!formData.title.trim()) {
        setFormData((prev) => ({
          ...prev,
          title: selectedFile.name.replace(/\.pdf$/i, '')
        }))
      }
    },
    [formData.title, t]
  )

  const handleDrag = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault()
    e.stopPropagation()
    if (e.type === 'dragenter' || e.type === 'dragover') setDragActive(true)
    if (e.type === 'dragleave') setDragActive(false)
  }, [])

  const handleDrop = useCallback(
    (e: React.DragEvent<HTMLDivElement>) => {
      e.preventDefault()
      e.stopPropagation()
      setDragActive(false)
      const dropped = e.dataTransfer.files?.[0]
      if (dropped) applySelectedFile(dropped)
    },
    [applySelectedFile]
  )

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = e.target.files?.[0]
    if (selected) applySelectedFile(selected)
  }

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    const { name, value } = e.target
    setFormData((prev) => ({ ...prev, [name]: value }))
  }

  const handleSelectChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const { name, value } = e.target
    setFormData((prev) => ({ ...prev, [name]: value }))
  }

  const resetForm = () => {
    setFile(null)
    setFormData({
      title: '',
      subject: '',
      concept_name: '',
      concept_overview: '',
      mastery_points: '',
      design_idea: '',
      grade_level: '',
      description: '',
      interests: ''
    })
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    setSuccess(null)

    if (!file) {
      setError(t('upload.error_upload_first'))
      return
    }

    if (!formData.title.trim()) {
      setError(t('upload.error_fill_title'))
      return
    }

    setUploading(true)
    try {
      const payload = new FormData()
      payload.append('file', file)
      payload.append('title', formData.title.trim())
      payload.append('subject', formData.subject.trim())
      if (formData.grade_level) {
        payload.append('grade_level', String(parseInt(formData.grade_level, 10)))
      }
payload.append('description', formData.description.trim())
      payload.append('is_public', String(isPublic))
      payload.append('generation_mode', generationMode)
      payload.append('ai_model', selectedModel)

      const conceptData = {
        subject: formData.subject.trim(),
        concept_name: formData.concept_name.trim(),
        concept_overview: formData.concept_overview.trim(),
        mastery_points: formData.mastery_points.trim(),
        design_idea: formData.design_idea.trim()
      }
      const hasConceptFocus = [
        conceptData.concept_name,
        conceptData.concept_overview,
        conceptData.mastery_points,
        conceptData.design_idea
      ].some(Boolean)

      // Send user preferences as JSON
      const userPreferences = {
        grade_level: formData.grade_level ? parseInt(formData.grade_level, 10) : undefined,
        interests: formData.interests
          ? formData.interests.split(',').map((s) => s.trim()).filter(Boolean)
          : [],
        include_exercises: includeExercises,
        include_prerequisites: includePrerequisites,
        concept_data: hasConceptFocus ? conceptData : undefined,
        pdf_generation_mode: hasConceptFocus ? 'pdf_with_concept_focus' : 'pdf_only',
        language: language  // Pass the current language preference
      }
      payload.append('user_preferences', JSON.stringify(userPreferences))

      const token = getAuthToken()
      if (!token) throw new Error(t('upload.error_login'))

      const response = await fetch(`${API_BASE_URL}/pdf/upload`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
        body: payload
      })

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(errorData.detail || errorData.message || `${t('upload.upload_failed')}${response.status}`)
      }

      const result: UploadResponse = await response.json()
      setSuccess(
        result.status === 'ready'
          ? `${t('upload.upload_success')}${result.original_filename}`
          : `${t('upload.uploaded_processing')}${result.original_filename}${t('upload.uploaded_processing_desc')}`
      )

      if (autoNavigate) {
        setTimeout(() => window.open(`/document/${result.id}`, '_blank'), 1200)
      }

      resetForm()
    } catch (err) {
      setError(err instanceof Error ? err.message : t('upload.error_upload_failed'))
    } finally {
      setUploading(false)
    }
  }

  return (
    <div className="space-y-6">
      {!hideTypeSelector && (
      <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
        <p className="mb-3 text-[16px] font-semibold text-slate-900">{t('upload.select_method')}</p>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
          <button
            type="button"
            onClick={() => setInputType('pdf')}
            className={`rounded-2xl border p-4 text-left transition ${
              inputType === 'pdf'
                ? 'border-[#7C4DFF] bg-[#f5f0ff] shadow-[0_10px_24px_rgba(124,77,255,0.18)]'
                : 'border-slate-200 bg-white hover:border-slate-300'
            }`}
          >
            <div className="flex items-start gap-3">
              <span className="inline-flex h-10 w-10 items-center justify-center rounded-xl bg-violet-100 text-violet-600">
                📄
              </span>
              <div>
                <p className="text-base font-semibold text-slate-900">{t('dashboard.upload_pdf')}</p>
                <p className="mt-1 text-[13px] text-slate-500">{t('dashboard.from_material')}</p>
              </div>
            </div>
          </button>

          <button
            type="button"
            onClick={() => setInputType('ppt')}
            className={`rounded-2xl border p-4 text-left transition ${
              inputType === 'ppt'
                ? 'border-[#7C4DFF] bg-[#f5f0ff] shadow-[0_10px_24px_rgba(124,77,255,0.18)]'
                : 'border-slate-200 bg-white hover:border-slate-300'
            }`}
          >
            <div className="flex items-start gap-3">
              <span className="inline-flex h-10 w-10 items-center justify-center rounded-xl bg-sky-100 text-sky-600">
                📊
              </span>
              <div>
                <p className="text-base font-semibold text-slate-900">{t('dashboard.upload_ppt')}</p>
                <p className="mt-1 text-[13px] text-slate-500">{t('dashboard.from_presentation')}</p>
              </div>
            </div>
          </button>

          <button
            type="button"
            onClick={() => setInputType('concept')}
            className={`rounded-2xl border p-4 text-left transition ${
              inputType === 'concept'
                ? 'border-[#7C4DFF] bg-[#f4efff] shadow-[0_10px_24px_rgba(124,77,255,0.18)]'
                : 'border-slate-200 bg-white hover:border-slate-300'
            }`}
          >
            <div className="flex items-start gap-3">
              <span className="inline-flex h-10 w-10 items-center justify-center rounded-xl bg-violet-100 text-violet-600">
                💡
              </span>
              <div>
                <p className="text-base font-semibold text-slate-900">{t('dashboard.input_concept')}</p>
                <p className="mt-1 text-[13px] text-slate-500">{t('dashboard.custom_generate')}</p>
              </div>
            </div>
          </button>
        </div>
      </div>
      )}

      {inputType === 'concept' ? (
        <ConceptInputForm />
      ) : inputType === 'ppt' ? (
        <PPTUploadForm embedded />
      ) : (
        <form onSubmit={handleSubmit} className="space-y-6">
          <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
            <p className="mb-3 text-[16px] font-semibold text-slate-900">{t('upload.upload_file')}</p>
            <div
              className={`relative rounded-2xl border-2 border-dashed p-8 text-center transition ${
                dragActive
                  ? 'border-[#7C4DFF] bg-[#f5f0ff]'
                  : file
                    ? 'border-emerald-300 bg-emerald-50'
                    : 'border-slate-300 bg-slate-50 hover:border-slate-400'
              }`}
              onDragEnter={handleDrag}
              onDragLeave={handleDrag}
              onDragOver={handleDrag}
              onDrop={handleDrop}
            >
              <input
                ref={fileInputRef}
                type="file"
                accept=".pdf"
                onChange={handleFileChange}
                className="hidden"
                disabled={uploading}
              />
              <div className="space-y-2">
                <p className="text-4xl">⬆</p>
                <p className="text-base font-semibold text-slate-800">{t('upload.drag_or_click')}</p>
                <p className="text-[13px] text-slate-500">{t('upload.support_pdf')}</p>
                <p className="pt-2 text-[13px] text-slate-500">{t('upload.example_pdf')}</p>
              </div>

              <div className="mt-5 flex flex-wrap items-center justify-center gap-2">
                <Button
                  type="button"
                  onClick={() => fileInputRef.current?.click()}
                  className="rounded-xl bg-[#7C4DFF] px-4 py-2 text-sm text-white hover:bg-[#6d3ff0]"
                >
                  {t('upload.select_file')}
                </Button>
              </div>
            </div>

            {file && (
              <div className="mt-4 rounded-xl border border-emerald-200 bg-emerald-50 p-3">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <p className="text-sm font-medium text-emerald-700">{file.name}</p>
                    <p className="text-xs text-emerald-600">{(file.size / 1024 / 1024).toFixed(2)} MB</p>
                  </div>
                  <div className="flex gap-2">
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() => fileInputRef.current?.click()}
                      className="rounded-lg px-3 py-1.5 text-xs"
                    >
                      {t('upload.replace')}
                    </Button>
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() => setFile(null)}
                      className="rounded-lg px-3 py-1.5 text-xs"
                    >
                      {t('upload.delete')}
                    </Button>
                  </div>
                </div>
              </div>
            )}
          </div>

          <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
            <p className="mb-3 text-[16px] font-semibold text-slate-900">{t('upload.basic_settings')}</p>
            <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
              <div className="md:col-span-3">
                <label htmlFor="title" className="mb-1 block text-[13px] font-medium text-slate-700">
                  {t('upload.title')}
                </label>
                <input
                  id="title"
                  name="title"
                  value={formData.title}
                  onChange={handleInputChange}
                  required
                  disabled={uploading}
                  className="w-full rounded-xl border border-slate-300 px-3 py-2.5 text-sm outline-none transition focus:border-[#7C4DFF] focus:ring-2 focus:ring-[#7C4DFF]/20"
                  placeholder={t('upload.title_placeholder')}
                />
              </div>

              <div>
                <label htmlFor="grade_level" className="mb-1 block text-[13px] font-medium text-slate-700">
                  {t('upload.grade')}
                </label>
                <select
                  id="grade_level"
                  name="grade_level"
                  value={formData.grade_level}
                  onChange={handleSelectChange}
                  disabled={uploading}
                  className="w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 text-sm outline-none transition focus:border-[#7C4DFF] focus:ring-2 focus:ring-[#7C4DFF]/20"
                >
                  <option value="">{t('upload.select_grade')}</option>
                  <option value="0">{t('grade.kindergarten')}</option>
                  <option value="1">{t('grade.grade1')}</option>
                  <option value="2">{t('grade.grade2')}</option>
                  <option value="3">{t('grade.grade3')}</option>
                  <option value="4">{t('grade.grade4')}</option>
                  <option value="5">{t('grade.grade5')}</option>
                  <option value="6">{t('grade.grade6')}</option>
                  <option value="7">{t('grade.grade7')}</option>
                  <option value="8">{t('grade.grade8')}</option>
                  <option value="9">{t('grade.grade9')}</option>
                  <option value="10">{t('grade.grade10')}</option>
                  <option value="11">{t('grade.grade11')}</option>
                  <option value="12">{t('grade.grade12')}</option>
                  <option value="13">{t('grade.college')}</option>
                  <option value="14">{t('grade.graduate')}</option>
                </select>
              </div>

              <div className="md:col-span-2">
                <label htmlFor="subject" className="mb-1 block text-[13px] font-medium text-slate-700">
                  {t('upload.subject')}
                </label>
                <input
                  id="subject"
                  name="subject"
                  value={formData.subject}
                  onChange={handleInputChange}
                  disabled={uploading}
                  className="w-full rounded-xl border border-slate-300 px-3 py-2.5 text-sm outline-none transition focus:border-[#7C4DFF] focus:ring-2 focus:ring-[#7C4DFF]/20"
                  placeholder={t('upload.subject_placeholder')}
                />
              </div>

              <div className="md:col-span-3 rounded-2xl border border-violet-100 bg-violet-50/60 p-4">
                <div className="mb-3">
                  <p className="text-[15px] font-semibold text-slate-900">
                    {language === 'zh' ? '知识点聚焦设置（可选）' : 'Concept Focus Settings (Optional)'}
                  </p>
                  <p className="mt-1 text-xs leading-5 text-slate-500">
                    {language === 'zh'
                      ? '上传 PDF 的同时指定核心知识点、掌握要点和设计思路，生成时会以 PDF 内容为依据，并围绕这些目标定制互动课程。'
                      : 'Upload a PDF while specifying the target concept, mastery goals, and design approach. Generation will use the PDF as source material and focus the course around these goals.'}
                  </p>
                </div>

                <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                  <div>
                    <label htmlFor="concept_name" className="mb-1 block text-[13px] font-medium text-slate-700">
                      {language === 'zh' ? '知识点名称（可选）' : 'Concept name (optional)'}
                    </label>
                    <input
                      id="concept_name"
                      name="concept_name"
                      value={formData.concept_name}
                      onChange={handleInputChange}
                      disabled={uploading}
                      className="w-full rounded-xl border border-slate-300 px-3 py-2.5 text-sm outline-none transition focus:border-[#7C4DFF] focus:ring-2 focus:ring-[#7C4DFF]/20"
                      placeholder={t('concept.name_placeholder')}
                    />
                  </div>

                  <div className="md:row-span-2">
                    <label htmlFor="design_idea" className="mb-1 block text-[13px] font-medium text-slate-700">
                      {language === 'zh' ? '设计思路（可选）' : 'Design idea (optional)'}
                    </label>
                    <textarea
                      id="design_idea"
                      name="design_idea"
                      value={formData.design_idea}
                      onChange={handleInputChange}
                      disabled={uploading}
                      rows={5}
                      className="w-full rounded-xl border border-slate-300 px-3 py-2.5 text-sm outline-none transition focus:border-[#7C4DFF] focus:ring-2 focus:ring-[#7C4DFF]/20"
                      placeholder={t('concept.design_idea_placeholder')}
                    />
                  </div>

                  <div>
                    <label htmlFor="concept_overview" className="mb-1 block text-[13px] font-medium text-slate-700">
                      {language === 'zh' ? '知识点概述（可选）' : 'Concept overview (optional)'}
                    </label>
                    <textarea
                      id="concept_overview"
                      name="concept_overview"
                      value={formData.concept_overview}
                      onChange={handleInputChange}
                      disabled={uploading}
                      rows={3}
                      className="w-full rounded-xl border border-slate-300 px-3 py-2.5 text-sm outline-none transition focus:border-[#7C4DFF] focus:ring-2 focus:ring-[#7C4DFF]/20"
                      placeholder={t('concept.overview_placeholder')}
                    />
                  </div>

                  <div className="md:col-span-2">
                    <label htmlFor="mastery_points" className="mb-1 block text-[13px] font-medium text-slate-700">
                      {language === 'zh' ? '学生掌握要点（可选）' : 'Mastery points (optional)'}
                    </label>
                    <textarea
                      id="mastery_points"
                      name="mastery_points"
                      value={formData.mastery_points}
                      onChange={handleInputChange}
                      disabled={uploading}
                      rows={3}
                      className="w-full rounded-xl border border-slate-300 px-3 py-2.5 text-sm outline-none transition focus:border-[#7C4DFF] focus:ring-2 focus:ring-[#7C4DFF]/20"
                      placeholder={t('concept.mastery_points_placeholder')}
                    />
                  </div>
                </div>
              </div>
            </div>
          </div>

          <details className="group rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
            <summary className="cursor-pointer list-none text-[16px] font-semibold text-slate-900">{t('upload.advanced_settings')}</summary>
            <div className="mt-4 space-y-4">
              <div>
                <label htmlFor="interests" className="mb-1 block text-[13px] font-medium text-slate-700">
                  {t('upload.interests')}
                </label>
                <input
                  id="interests"
                  name="interests"
                  value={formData.interests}
                  onChange={handleInputChange}
                  disabled={uploading}
                  className="w-full rounded-xl border border-slate-300 px-3 py-2.5 text-sm outline-none transition focus:border-[#7C4DFF] focus:ring-2 focus:ring-[#7C4DFF]/20"
                  placeholder={t('upload.interests_placeholder')}
                />
              </div>

              <div>
                <label htmlFor="description" className="mb-1 block text-[13px] font-medium text-slate-700">
                  {t('upload.description')}
                </label>
                <textarea
                  id="description"
                  name="description"
                  rows={3}
                  value={formData.description}
                  onChange={handleInputChange}
                  disabled={uploading}
                  className="w-full rounded-xl border border-slate-300 px-3 py-2.5 text-sm outline-none transition focus:border-[#7C4DFF] focus:ring-2 focus:ring-[#7C4DFF]/20"
                  placeholder={t('upload.description_placeholder')}
                />
              </div>

              <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
                <label className="flex items-center gap-2 text-sm text-slate-700">
                  <input
                    type="checkbox"
                    checked={isPublic}
                    onChange={(e) => setIsPublic(e.target.checked)}
                    disabled={uploading}
                    className="h-4 w-4 rounded border-slate-300 text-[#7C4DFF] focus:ring-[#7C4DFF]"
                  />
                  {t('upload.make_public')}
                </label>

                <label className="flex items-center gap-2 text-sm text-slate-700">
                  <input
                    type="checkbox"
                    checked={autoNavigate}
                    onChange={(e) => setAutoNavigate(e.target.checked)}
                    disabled={uploading}
                    className="h-4 w-4 rounded border-slate-300 text-[#7C4DFF] focus:ring-[#7C4DFF]"
                  />
                  {t('upload.auto_open')}
                </label>

                <label className="flex items-center gap-2 text-sm text-slate-700">
                  <input
                    type="checkbox"
                    checked={includeExercises}
                    onChange={(e) => setIncludeExercises(e.target.checked)}
                    disabled={uploading}
                    className="h-4 w-4 rounded border-slate-300 text-[#7C4DFF] focus:ring-[#7C4DFF]"
                  />
                  {t('upload.include_exercises')}
                </label>

                <label className="flex items-center gap-2 text-sm text-slate-700">
                  <input
                    type="checkbox"
                    checked={includePrerequisites}
                    onChange={(e) => setIncludePrerequisites(e.target.checked)}
                    disabled={uploading}
                    className="h-4 w-4 rounded border-slate-300 text-[#7C4DFF] focus:ring-[#7C4DFF]"
                  />
                  {t('upload.include_prerequisites')}
                </label>
              </div>
            </div>
          </details>

          <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
            <p className="mb-3 text-[16px] font-semibold text-slate-900">{t('upload.generation_mode')}</p>
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              <label
                className={`cursor-pointer rounded-2xl border p-4 transition ${
                  generationMode === 'fast'
                    ? 'border-[#7C4DFF] bg-[#f5f0ff] shadow-[0_10px_24px_rgba(124,77,255,0.15)]'
                    : 'border-slate-300 bg-white hover:border-slate-400'
                }`}
              >
                <input
                  type="radio"
                  name="generationMode"
                  value="fast"
                  checked={generationMode === 'fast'}
                  onChange={() => setGenerationMode('fast')}
                  className="sr-only"
                />
                <p className="text-base font-semibold text-slate-900">{t('upload.fast_mode')}</p>
                <p className="mt-1 text-[13px] text-slate-500">{t('upload.fast_mode_desc')}</p>
              </label>

              <label
                className={`relative cursor-pointer rounded-2xl border p-4 transition ${
                  generationMode === 'heavy'
                    ? 'border-[#7C4DFF] bg-[#f4efff] shadow-[0_10px_24px_rgba(124,77,255,0.15)]'
                    : 'border-slate-300 bg-white hover:border-slate-400'
                }`}
              >
                <span className="absolute right-3 top-3 rounded-full bg-[#7C4DFF] px-2 py-0.5 text-[11px] font-semibold text-white">
                  {t('upload.recommended')}
                </span>
                <input
                  type="radio"
                  name="generationMode"
                  value="heavy"
                  checked={generationMode === 'heavy'}
                  onChange={() => setGenerationMode('heavy')}
                  className="sr-only"
                />
                <p className="text-base font-semibold text-slate-900">{t('upload.heavy_mode')}</p>
                <p className="mt-1 text-[13px] text-slate-500">{t('upload.heavy_mode_desc')}</p>
              </label>
            </div>
          </div>

          {error && <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}
          {success && <div className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">{success}</div>}

          <div className="sticky bottom-0 z-10 rounded-2xl border border-slate-200 bg-white/90 p-3 shadow-[0_-8px_20px_rgba(15,23,42,0.08)] backdrop-blur">
            <Button
              type="submit"
              disabled={uploading || !file}
              className="h-12 w-full rounded-xl bg-gradient-to-r from-[#7C4DFF] to-[#A855F7] text-base font-semibold text-white shadow-[0_12px_26px_rgba(124,77,255,0.3)] hover:from-[#6d3ff0] hover:to-[#9333ea]"
            >
              {uploading ? t('upload.processing') : t('upload.generate_button')}
            </Button>
          </div>
        </form>
      )}
    </div>
  )
}
