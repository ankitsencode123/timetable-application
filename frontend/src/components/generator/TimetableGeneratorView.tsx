import React, { useState, useEffect, useMemo } from 'react'
import {
  Sparkles, Play, CheckCircle2, AlertTriangle, AlertCircle, RefreshCw,
  Save, Download, Copy, Calendar, Clock, BookOpen, Users, MapPin,
  Sliders, ArrowRight, Eye, Check, ChevronDown, ChevronUp, Trash2, Plus, Zap, Award,
  Upload, FileText, Layers, X, HelpCircle, Pencil, Search, Filter, KeyRound
} from 'lucide-react'
import {
  getGeneratorPresets, validateGeneratorBoard, runAutomatedGenerator,
  saveGeneratedTimetableAsDraft, getVersion, extractGeneratorFiles, getGeneratorTemplates,
  createGeneratorFaculty, createGeneratorSubject
} from '../../api'
import type {
  GeneratorBoard, GeneratorResult, GeneratorIssue, GeneratorScheduledClass,
  GeneratorSemester, GeneratorRoom, GeneratorTeacher, GeneratorSubject, ExtractedFilesData,
  CreatedAccountInfo
} from '../../types'
import { useWorkspaceStore } from '../../store'
import { formatTeacherShortName, deriveTeacherShortForm } from '../../utils'

const DEFAULT_SUBJECTS_TEMPLATE = `Subject Code,Subject Name,Semester,Internal Teacher ID,External Teacher ID,Class Type,Sessions Per Week,Facility
CS301,Data Structures,B.Tech 3rd,I01,E01,Theory,2.5,none
CS302,Database Systems,B.Tech 3rd,I02,E02,Theory,3,none
MA301,Discrete Mathematics,B.Tech 3rd,I03,,Theory,2.5,blackboard
CS351,Data Structures Lab,B.Tech 3rd,I01,,Practical,1,none
CS601,Advanced Algorithms,M.Tech 1st,I01,E01,Theory,2.5,none
CS651,Algorithms Lab,M.Tech 1st,I03,,Practical,2,none
MS201,Linear Algebra,M.Sc. 2nd,I04,E02,Theory,2.5,blackboard
MS251,Computing Lab,M.Sc. 2nd,I04,,Practical,1,none
`

const DEFAULT_TEACHERS_TEMPLATE = `Teacher ID,Name,Pref 1 Day,Pref 1 Time,Pref 1 Facility,Pref 2 Day,Pref 2 Time,Pref 2 Facility,Pref 3 Day,Pref 3 Time,Pref 3 Facility
I01,Dr. A. Rao,Mon,10-12:00,blackboard and projector,Wed,12-2,projector,Fri,10-12:00,black board
I02,Dr. B. Iyer,Tue,10-12:00,black board,Thu,2:30-4:30,blackboard and projector,Mon,12-2,projector
I03,Prof. C. Das,Mon,10-12:00,black board,Tue,12-2,black board,Thu,10-12:00,black board
I04,Dr. D. Menon,Wed,10-12:00,projector,Thu,12-2,both,Fri,2:30-4:30,projector
E01,Mr. K. Patel (ext),Tue,10-12:00,projector,Wed,10-12:00,projector,Thu,10-12:00,both
E02,Ms. L. Gupta (ext),Tue,12-2,both,Thu,12-2,both,Wed,12-2,both
`

export default function TimetableGeneratorView() {
  const { setCurrentVersion, setVersions, setSidebarTab } = useWorkspaceStore()

  // Preset & Board state
  const [board, setBoard] = useState<GeneratorBoard | null>(null)
  const [loadingPresets, setLoadingPresets] = useState(true)
  const [activeTab, setActiveTab] = useState<'config' | 'results' | 'matrix'>('config')

  // Validation & Generation state
  const [validating, setValidating] = useState(false)
  const [validationIssues, setValidationIssues] = useState<GeneratorIssue[]>([])
  const [generating, setGenerating] = useState(false)
  const [generationResult, setGenerationResult] = useState<GeneratorResult | null>(null)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const [issueFilter, setIssueFilter] = useState<'all' | 'error' | 'warning'>('all')
  const [issuesExpanded, setIssuesExpanded] = useState(true)

  // Results View Sub-tabs
  const [matrixViewMode, setMatrixViewMode] = useState<'semester' | 'teacher' | 'all' | 'preferences'>('semester')
  const [selectedSemesterId, setSelectedSemesterId] = useState<string>('ALL')
  const [selectedTeacherId, setSelectedTeacherId] = useState<string>('')
  const [searchTerm, setSearchTerm] = useState('')

  // Saving state
  const [savingDraft, setSavingDraft] = useState(false)
  const [saveSuccessMsg, setSaveSuccessMsg] = useState<string | null>(null)
  const [savedVersionId, setSavedVersionId] = useState<number | null>(null)

  // Created teacher accounts feedback
  const [createdAccounts, setCreatedAccounts] = useState<CreatedAccountInfo[]>([])
  const [showAccountsModal, setShowAccountsModal] = useState(false)
  const [latestSingleAccount, setLatestSingleAccount] = useState<{ name: string; short: string; email: string; password?: string } | null>(null)
  const [copiedAccounts, setCopiedAccounts] = useState(false)
  const catalogTeachers = useWorkspaceStore(s => s.catalogTeachers)

  // File Extraction Modal State
  const [showImportModal, setShowImportModal] = useState(false)
  const [subjectsFileContent, setSubjectsFileContent] = useState('')
  const [teachersFileContent, setTeachersFileContent] = useState('')
  const [subjectsFileName, setSubjectsFileName] = useState('')
  const [teachersFileName, setTeachersFileName] = useState('')
  const [extracting, setExtracting] = useState(false)
  const [extractedData, setExtractedData] = useState<ExtractedFilesData | null>(null)
  const [activeImportTab, setActiveImportTab] = useState<'subjects' | 'teachers' | 'preview'>('subjects')

  // Manual Entity Modals
  const [showAddSemesterModal, setShowAddSemesterModal] = useState(false)
  const [newSemester, setNewSemester] = useState<{ programme: string; number: number; id: string; name: string; strength: number }>({
    programme: 'B.Tech',
    number: 3,
    id: 'BT-3',
    name: 'B.Tech 3rd Semester',
    strength: 60,
  })

  const [showAddRoomModal, setShowAddRoomModal] = useState(false)
  const [newRoom, setNewRoom] = useState<{ number: string; kind: 'classroom' | 'lab'; facility: string; capacity: number }>({
    number: 'CR-101',
    kind: 'classroom',
    facility: 'blackboard',
    capacity: 60,
  })

  const [showAddTeacherModal, setShowAddTeacherModal] = useState(false)
  const [newTeacher, setNewTeacher] = useState<{
    id: string
    name: string
    internal: boolean
    pref1_day: string
    pref1_slot: number
    pref1_facility: string
    pref2_day: string
    pref2_slot: number
    pref2_facility: string
    pref3_day: string
    pref3_slot: number
    pref3_facility: string
    unavailable_days: string[]
  }>({
    id: 'T101',
    name: 'Dr. New Faculty',
    internal: true,
    pref1_day: 'Mon',
    pref1_slot: 1,
    pref1_facility: 'blackboard and projector',
    pref2_day: 'Wed',
    pref2_slot: 2,
    pref2_facility: 'projector',
    pref3_day: 'Fri',
    pref3_slot: 1,
    pref3_facility: 'black board',
    unavailable_days: [],
  })

  const [showAddSubjectModal, setShowAddSubjectModal] = useState(false)
  const [newSubject, setNewSubject] = useState<{
    code: string
    name: string
    semester_id: string
    internal_teacher_id: string
    external_teacher_id: string
    class_type: 'theory' | 'practical'
    sessions_per_week: number
    facility: string
  }>({
    code: 'CS301',
    name: 'New Course Title',
    semester_id: '',
    internal_teacher_id: '',
    external_teacher_id: '',
    class_type: 'theory',
    sessions_per_week: 3,
    facility: 'none',
  })

  // Editing Modals State
  const [editingTeacherIdx, setEditingTeacherIdx] = useState<number | null>(null)
  const [editingTeacher, setEditingTeacher] = useState<GeneratorTeacher | null>(null)

  const [editingSubjectIdx, setEditingSubjectIdx] = useState<number | null>(null)
  const [editingSubject, setEditingSubject] = useState<GeneratorSubject | null>(null)

  const [editingSemesterIdx, setEditingSemesterIdx] = useState<number | null>(null)
  const [editingSemester, setEditingSemester] = useState<GeneratorSemester | null>(null)

  const [editingRoomIdx, setEditingRoomIdx] = useState<number | null>(null)
  const [editingRoom, setEditingRoom] = useState<GeneratorRoom | null>(null)

  // Unscheduled classes inspection & filtering state
  const [expandedUnscheduledIdxs, setExpandedUnscheduledIdxs] = useState<Record<number, boolean>>({})
  const [unscheduledFilterSem, setUnscheduledFilterSem] = useState<string>('ALL')
  const [unscheduledSearch, setUnscheduledSearch] = useState<string>('')

  function openEditSubjectByCode(code: string) {
    if (!board) return
    const idx = board.subjects.findIndex(s => s.code === code)
    if (idx >= 0) {
      setEditingSubjectIdx(idx)
      setEditingSubject({ ...board.subjects[idx] })
    }
  }

  function openEditTeacherById(id: string) {
    if (!board) return
    const idx = board.teachers.findIndex(t => t.id === id)
    if (idx >= 0) {
      setEditingTeacherIdx(idx)
      setEditingTeacher({
        ...board.teachers[idx],
        preferences: [...board.teachers[idx].preferences],
        unavailable_days: [...board.teachers[idx].unavailable_days],
      })
    }
  }

  // Auto-validate helper after edits
  async function autoValidateBoard(updatedBoard: GeneratorBoard) {
    try {
      const res = await validateGeneratorBoard(updatedBoard)
      setValidationIssues(res.issues || [])
    } catch {
      /* best effort */
    }
  }

  // Load presets on mount
  useEffect(() => {
    async function load() {
      setLoadingPresets(true)
      try {
        const presets = await getGeneratorPresets()
        if (presets?.demo) {
          setBoard(presets.demo)
          setSelectedSemesterId('ALL')
          if (presets.demo.teachers.length > 0) {
            setSelectedTeacherId(presets.demo.teachers[0].id)
          }
        }
      } catch (err: any) {
        setErrorMsg('Failed to load presets: ' + (err.message || String(err)))
      } finally {
        setLoadingPresets(false)
      }
    }
    load()
  }, [])

  // Fast reset/load handlers
  async function handleLoadPreset(type: 'demo' | 'catalog') {
    setErrorMsg(null)
    setSaveSuccessMsg(null)
    setLoadingPresets(true)
    try {
      const presets = await getGeneratorPresets()
      const chosen = presets[type]
      if (chosen) {
        setBoard(chosen)
        setValidationIssues([])
        setSelectedSemesterId('ALL')
        if (chosen.teachers.length > 0) setSelectedTeacherId(chosen.teachers[0].id)
      }
    } catch (err: any) {
      setErrorMsg('Failed to switch preset: ' + (err.message || String(err)))
    } finally {
      setLoadingPresets(false)
    }
  }

  // Blank slate handler
  function handleClearBoard() {
    if (board && (board.semesters.length > 0 || board.subjects.length > 0 || board.teachers.length > 0)) {
      if (!window.confirm('Clear all semesters, courses, and teachers to start completely from scratch?')) {
        return
      }
    }
    const emptyBoard: GeneratorBoard = {
      config: {
        seed: null,
        time_limit_s: 6.0,
        working_days: ['Mon', 'Tue', 'Wed', 'Thu', 'Fri'],
        allow_extension_slots: true,
        default_theory_sessions: 3,
        default_practical_sessions: 1,
        full_session_minutes: 120,
        short_session_minutes: 60,
        allow_split_sessions: true,
        max_split_rounds: 4,
        allow_short_in_regular_slot: false,
      },
      slots: [
        { id: 1, label: 'Slot 1 (10-12:00)', start: '10:00', end: '12:00', tier: 0 },
        { id: 2, label: 'Slot 2 (12-2)', start: '12:00', end: '14:00', tier: 0 },
        { id: 3, label: 'Slot 3 (2:30-4:30)', start: '14:30', end: '16:30', tier: 0 },
      ],
      extension_slots: [
        { id: 4, label: 'Ext 1h (16:30-17:30)', start: '16:30', end: '17:30', tier: 1 },
      ],
      rooms: [
        { number: 'CR-101', kind: 'classroom', facility: 'blackboard', capacity: 60 },
        { number: 'CR-102', kind: 'classroom', facility: 'projector', capacity: 60 },
        { number: 'CR-103', kind: 'classroom', facility: 'both', capacity: 60 },
        { number: 'Lab-1', kind: 'lab', facility: 'blackboard', capacity: 40 },
        { number: 'Lab-2', kind: 'lab', facility: 'projector', capacity: 40 },
      ],
      semesters: [],
      teachers: [],
      subjects: [],
    }
    setBoard(emptyBoard)
    setGenerationResult(null)
    setValidationIssues([])
    setSelectedSemesterId('ALL')
    setSelectedTeacherId('')
    setSaveSuccessMsg(null)
  }

  // Pre-flight validation
  async function handleValidate() {
    if (!board) return
    setValidating(true)
    setErrorMsg(null)
    try {
      const res = await validateGeneratorBoard(board)
      setValidationIssues(res.issues || [])
    } catch (err: any) {
      setErrorMsg('Validation failed: ' + (err.message || String(err)))
    } finally {
      setValidating(false)
    }
  }

  // Generate timetable
  async function handleGenerate() {
    if (!board) return
    setGenerating(true)
    setErrorMsg(null)
    setSaveSuccessMsg(null)
    try {
      const res = await runAutomatedGenerator({ board, strict: false })
      setGenerationResult(res)
      setValidationIssues(res.issues || [])
      setActiveTab('results')
    } catch (err: any) {
      setErrorMsg('Generation error: ' + (err.message || String(err)))
    } finally {
      setGenerating(false)
    }
  }

  // Save generated classes as draft into the main timetable system
  async function handleSaveDraft(publishImmediately = false) {
    if (!generationResult || !generationResult.classes.length) return
    setSavingDraft(true)
    setErrorMsg(null)
    try {
      const res = await saveGeneratedTimetableAsDraft({
        classes: generationResult.classes,
        change_summary: `Automated Generation (${generationResult.classes.length} classes, 0 violations)`,
        publish_immediately: publishImmediately,
      })
      setSavedVersionId(res.version_id)
      setSaveSuccessMsg(res.message)
      if (res.created_accounts && res.created_accounts.length > 0) {
        setCreatedAccounts(res.created_accounts)
        setShowAccountsModal(true)
      }
      useWorkspaceStore.getState().loadCatalog()

      try {
        const detail = await getVersion(res.version_id)
        setCurrentVersion(detail.id, detail.entries)
      } catch { /* ignore */ }
    } catch (err: any) {
      setErrorMsg('Failed to save to database: ' + (err.message || String(err)))
    } finally {
      setSavingDraft(false)
    }
  }

  // File download helper
  function downloadCSVFile(filename: string, content: string) {
    const blob = new Blob([content], { type: 'text/csv;charset=utf-8;' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    link.click()
    URL.revokeObjectURL(url)
  }

  // Export handlers
  function handleDownloadCSV() {
    if (!generationResult) return
    const headers = ["semester", "programme", "day", "slot", "start", "end", "duration_hours", "is_half_session", "is_split_piece", "subject_code", "subject_name", "type", "teacher_id", "teacher", "room", "preference_rank", "extension_slot"]
    const rows = generationResult.classes.map(c => [
      c.semester_id, c.programme, c.day, c.slot_title, c.start, c.end, c.duration_hours ?? 2.0, c.is_half_session ? "1" : "0", c.is_split_piece ? "1" : "0", c.subject_code,
      `"${c.subject_name.replace(/"/g, '""')}"`, c.class_type, c.teacher_id, `"${c.teacher_name.replace(/"/g, '""')}"`,
      c.room, c.preference_rank || "", c.extension_slot ? "1" : "0"
    ])
    const csvContent = [headers.join(","), ...rows.map(r => r.join(","))].join("\n")
    downloadCSVFile(`timetable_automated_${Date.now()}.csv`, csvContent)
  }

  function handleDownloadJSON() {
    if (!generationResult) return
    const blob = new Blob([JSON.stringify(generationResult, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement("a")
    link.href = url
    link.download = `timetable_automated_${Date.now()}.json`
    link.click()
    URL.revokeObjectURL(url)
  }

  // File Extraction Handlers
  async function handleExtractFiles() {
    if (!subjectsFileContent.trim() && !teachersFileContent.trim()) {
      setErrorMsg('Please upload or paste at least one file (Subject Allocations or Teacher Preferences).')
      return
    }
    setExtracting(true)
    setErrorMsg(null)
    try {
      const data = await extractGeneratorFiles({
        subjects_file_content: subjectsFileContent || undefined,
        teachers_file_content: teachersFileContent || undefined,
      })
      setExtractedData(data)
      if (data.created_accounts && data.created_accounts.length > 0) {
        setCreatedAccounts(data.created_accounts)
      }
      useWorkspaceStore.getState().loadCatalog()
      setActiveImportTab('preview')
    } catch (err: any) {
      setErrorMsg('Extraction error: ' + (err.message || String(err)))
    } finally {
      setExtracting(false)
    }
  }

  function handleApplyExtractedData(mode: 'replace' | 'merge') {
    if (!extractedData || !board) return

    if (mode === 'replace') {
      const updated: GeneratorBoard = {
        ...board,
        semesters: extractedData.semesters,
        subjects: extractedData.subjects,
        teachers: extractedData.teachers,
      }
      setBoard(updated)
      if (updated.semesters.length > 0) setSelectedSemesterId('ALL')
      if (updated.teachers.length > 0) setSelectedTeacherId(updated.teachers[0].id)
    } else {
      // Merge semesters
      const semMap = new Map<string, GeneratorSemester>()
      board.semesters.forEach(s => semMap.set(s.id, s))
      extractedData.semesters.forEach(s => semMap.set(s.id, s))

      // Merge teachers
      const teacherMap = new Map<string, GeneratorTeacher>()
      board.teachers.forEach(t => teacherMap.set(t.id, t))
      extractedData.teachers.forEach(t => teacherMap.set(t.id, t))

      // Merge subjects
      const subMap = new Map<string, GeneratorSubject>()
      board.subjects.forEach(s => subMap.set(s.code, s))
      extractedData.subjects.forEach(s => subMap.set(s.code, s))

      const updated: GeneratorBoard = {
        ...board,
        semesters: Array.from(semMap.values()),
        teachers: Array.from(teacherMap.values()),
        subjects: Array.from(subMap.values()),
      }
      setBoard(updated)
    }

    if (extractedData.created_accounts && extractedData.created_accounts.length > 0) {
      setCreatedAccounts(extractedData.created_accounts)
      setShowAccountsModal(true)
    }
    useWorkspaceStore.getState().loadCatalog()

    setShowImportModal(false)
    setSaveSuccessMsg(`Successfully imported ${extractedData.summary.semesters_count} semesters, ${extractedData.summary.subjects_count} subjects, and ${extractedData.summary.teachers_count} teachers! Automatically registered to catalog & user accounts generated.`)
    setActiveTab('config')
  }

  // Handle local file read for drag-and-drop / file picker
  function handleReadFile(file: File, target: 'subjects' | 'teachers') {
    const reader = new FileReader()
    reader.onload = (e) => {
      const text = e.target?.result as string || ''
      if (target === 'subjects') {
        setSubjectsFileContent(text)
        setSubjectsFileName(file.name)
      } else {
        setTeachersFileContent(text)
        setTeachersFileName(file.name)
      }
    }
    reader.readAsText(file)
  }

  // Render single semester timetable grid
  function renderSemesterGrid(sem: GeneratorSemester) {
    if (!board || !generationResult) return null

    const semClasses = generationResult.classes.filter(c => c.semester_id === sem.id)
    const gridMap = new Map<string, GeneratorScheduledClass>()
    semClasses.forEach(c => gridMap.set(`${c.day}_${c.slot_id}`, c))

    const allSlots = [...board.slots, ...(board.config.allow_extension_slots ? board.extension_slots : [])]
    allSlots.sort((a, b) => a.id - b.id)

    return (
      <div key={sem.id} className="card" style={{ padding: 'var(--sp-4)', background: 'var(--card-bg)', border: '1px solid var(--line)', borderRadius: 'var(--radius)', overflowX: 'auto', marginBottom: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12, flexWrap: 'wrap', gap: 8 }}>
          <div>
            <h3 style={{ fontFamily: 'var(--font-display)', fontSize: 'var(--fs-md)', fontWeight: 700, color: 'var(--ink)', margin: 0, display: 'flex', alignItems: 'center', gap: 8 }}>
              <span>{sem.id}</span>
              <span style={{ fontSize: 'var(--fs-sm)', fontWeight: 500, color: 'var(--ink-soft)' }}>
                ({sem.programme} Semester {sem.number}{sem.name ? ` · ${sem.name}` : ''})
              </span>
            </h3>
            {sem.strength && (
              <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--ink-soft)', marginTop: 2 }}>
                Cohort strength: {sem.strength} students
              </div>
            )}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span className="badge" style={{ background: 'rgba(0, 139, 135, 0.1)', color: 'var(--accent)', fontWeight: 600 }}>
              {semClasses.length} sessions scheduled
            </span>
            <button
              className="btn btn-ghost btn-sm"
              style={{ fontSize: 11, padding: '2px 8px' }}
              onClick={() => {
                setSelectedSemesterId(sem.id)
              }}
              title="Focus solely on this semester"
            >
              Focus View
            </button>
          </div>
        </div>

        <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left', fontSize: 'var(--fs-xs)' }}>
          <thead>
            <tr style={{ background: 'var(--paper)', borderBottom: '2px solid var(--line)' }}>
              <th style={{ padding: '10px 12px', width: 140 }}>Time / Slot</th>
              {board.config.working_days.map(d => (
                <th key={d} style={{ padding: '10px 12px', minWidth: 160 }}>{d}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {allSlots.map(slot => (
              <tr key={slot.id} style={{ borderBottom: '1px solid var(--line)' }}>
                <td style={{ padding: '12px', background: 'var(--paper)', fontWeight: 600, color: 'var(--ink)' }}>
                  <div>{slot.label || `Slot ${slot.id}`}</div>
                  <div style={{ color: 'var(--ink-soft)', fontWeight: 400, fontSize: 10, marginTop: 2 }}>{slot.start}–{slot.end}</div>
                  {slot.tier > 0 && <span style={{ background: 'rgba(245, 158, 11, 0.1)', color: '#b45309', padding: '1px 4px', borderRadius: 3, fontSize: 9 }}>Ext</span>}
                </td>
                {board.config.working_days.map(day => {
                  const cls = gridMap.get(`${day}_${slot.id}`)
                  if (!cls) {
                    return (
                      <td key={day} style={{ padding: '12px', color: 'var(--ink-soft)', textAlign: 'center', background: 'rgba(0,0,0,0.01)' }}>
                        —
                      </td>
                    )
                  }
                  return (
                    <td key={day} style={{ padding: '8px', verticalAlign: 'top' }}>
                      <div style={{
                        padding: 8, borderRadius: 6,
                        background: cls.class_type.toLowerCase() === 'practical' ? 'rgba(245, 158, 11, 0.08)' : 'rgba(0, 139, 135, 0.08)',
                        borderLeft: `3px solid ${cls.class_type.toLowerCase() === 'practical' ? '#f59e0b' : 'var(--accent)'}`,
                      }}>
                        <div style={{ fontWeight: 700, color: 'var(--ink)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                          <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                            {cls.subject_code}
                            {cls.is_half_session && (
                              <span style={{
                                background: cls.is_split_piece ? 'rgba(168, 85, 247, 0.15)' : 'rgba(59, 130, 246, 0.15)',
                                color: cls.is_split_piece ? '#7e22ce' : '#1d4ed8',
                                fontSize: 9, padding: '1px 4px', borderRadius: 3, fontWeight: 700
                              }}>
                                {cls.is_split_piece ? '1h Split' : '1h'}
                              </span>
                            )}
                          </span>
                          {cls.preference_rank && (
                            <span style={{ background: '#10b981', color: '#fff', fontSize: 9, padding: '1px 4px', borderRadius: 3, fontWeight: 700 }}>
                              Pref #{cls.preference_rank}
                            </span>
                          )}
                        </div>
                        <div style={{ fontSize: 10, color: 'var(--ink-soft)', marginTop: 4, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                          <span title={cls.teacher_name}>
                            <strong style={{ color: 'var(--ink)' }}>{formatTeacherShortName(cls.teacher_id, cls.subject_name, catalogTeachers)}</strong>
                            {cls.teacher_name && cls.teacher_name !== cls.teacher_id && (
                              <span style={{ marginLeft: 4, opacity: 0.8 }}>({cls.teacher_name})</span>
                            )}
                          </span>
                          <span style={{ fontWeight: 600, color: 'var(--ink)' }}>Rm {cls.room}</span>
                        </div>
                        {cls.extension_slot && (
                          <div style={{ marginTop: 2, fontSize: 9, color: '#b45309', fontWeight: 600 }}>
                            Ext Slot (4:30–5:30 PM)
                          </div>
                        )}
                      </div>
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )
  }

  if (loadingPresets || !board) {
    return (
      <div style={{ padding: 'var(--sp-6)', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', minHeight: '60vh', gap: 16 }}>
        <RefreshCw size={28} className="spin" style={{ color: 'var(--accent)' }} />
        <div style={{ fontFamily: 'var(--font-display)', fontSize: 'var(--fs-md)', color: 'var(--ink)' }}>Loading Automated Timetable Generator…</div>
      </div>
    )
  }

  const errorIssues = validationIssues.filter(i => i.level === 'error')
  const warningIssues = validationIssues.filter(i => i.level === 'warning')

  return (
    <div style={{ padding: 'var(--sp-5)', maxWidth: 1320, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 'var(--sp-4)' }}>
      {/* Top Hero Banner */}
      <div style={{
        background: 'linear-gradient(135deg, rgba(0, 139, 135, 0.08) 0%, rgba(20, 32, 51, 0.04) 100%)',
        border: '1px solid var(--line)', borderRadius: 'var(--radius-lg)', padding: 'var(--sp-4) var(--sp-5)',
        display: 'flex', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between', gap: 16,
      }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
            <span style={{
              background: 'var(--ink)', color: 'var(--paper)', fontSize: 10, fontFamily: 'var(--font-mono)',
              padding: '2px 8px', borderRadius: 4, fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase'
            }}>
              CSP + Simulated Annealing
            </span>
            <span style={{
              background: 'rgba(0, 139, 135, 0.15)', color: 'var(--accent)', fontSize: 11, fontFamily: 'var(--font-mono)',
              padding: '2px 8px', borderRadius: 4, fontWeight: 600
            }}>
              Complete User-Driven Input
            </span>
          </div>
          <h1 style={{ fontFamily: 'var(--font-display)', fontSize: 'var(--fs-xl)', fontWeight: 700, color: 'var(--ink)', margin: 0 }}>
            Automated Full-Institution Timetable Generator
          </h1>
          <p style={{ margin: '4px 0 0', fontSize: 'var(--fs-sm)', color: 'var(--ink-soft)' }}>
            Single-click conflict-free institution generation with constraint satisfaction, ejection-chain repair, and soft-cost preference optimization.
          </p>
        </div>

        {/* Global Toolbar Controls */}
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8 }}>
          <button
            className="btn btn-primary btn-sm"
            style={{ background: 'var(--accent)', color: '#fff', gap: 6 }}
            onClick={() => setShowImportModal(true)}
            title="Import curriculum files for B.Tech, M.Tech, M.Sc and teacher preferences"
          >
            <Upload size={13} /> Import Data Files
          </button>

          <button
            className="btn btn-ghost btn-sm"
            onClick={handleClearBoard}
            title="Wipe board and start with a blank clean slate"
            style={{ color: 'var(--violation)', gap: 4 }}
          >
            <Trash2 size={13} /> Blank Slate
          </button>

          <button className="btn btn-ghost btn-sm" onClick={() => handleLoadPreset('demo')} title="Reset to standard university benchmark dataset">
            🎓 Demo Benchmark
          </button>

          <button className="btn btn-ghost btn-sm" onClick={() => handleLoadPreset('catalog')} title="Build from current database catalog">
            🏛️ DB Catalog
          </button>

          <button
            className="btn btn-primary btn-sm"
            style={{ background: 'var(--ink)', color: 'var(--paper)', gap: 6 }}
            onClick={handleGenerate}
            disabled={generating}
          >
            {generating ? <RefreshCw size={13} className="spin" /> : <Play size={13} fill="currentColor" />}
            {generating ? 'Optimizing…' : 'Run Generator'}
          </button>
        </div>
      </div>

      {/* Global Alerts */}
      {errorMsg && (
        <div style={{ background: 'rgba(239, 68, 68, 0.1)', border: '1px solid var(--violation)', borderRadius: 'var(--radius)', padding: '12px 16px', display: 'flex', alignItems: 'center', gap: 10, color: 'var(--violation)', fontSize: 'var(--fs-sm)' }}>
          <AlertCircle size={18} style={{ flexShrink: 0 }} />
          <div style={{ flex: 1 }}>{errorMsg}</div>
          <button className="btn-icon" onClick={() => setErrorMsg(null)}>×</button>
        </div>
      )}

      {saveSuccessMsg && (
        <div style={{ background: 'rgba(16, 185, 129, 0.1)', border: '1px solid #10b981', borderRadius: 'var(--radius)', padding: '12px 16px', display: 'flex', alignItems: 'center', gap: 10, color: '#065f46', fontSize: 'var(--fs-sm)' }}>
          <CheckCircle2 size={18} style={{ flexShrink: 0, color: '#10b981' }} />
          <div style={{ flex: 1 }}>
            <strong>Success!</strong> {saveSuccessMsg}
          </div>
          {savedVersionId && (
            <button className="btn btn-sm btn-primary" onClick={() => setSidebarTab('dashboard')}>
              View in Studio →
            </button>
          )}
        </div>
      )}

      {/* Single Faculty Account Generated Banner */}
      {latestSingleAccount && (
        <div style={{
          background: 'rgba(0, 139, 135, 0.08)',
          border: '1px solid var(--accent)',
          borderRadius: 'var(--radius)',
          padding: '12px 16px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 12,
          fontSize: 'var(--fs-sm)',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <KeyRound size={20} style={{ color: 'var(--accent)', flexShrink: 0 }} />
            <div>
              <strong>Faculty Account Created:</strong> {latestSingleAccount.name} (<span style={{ fontFamily: 'var(--font-mono)' }}>{latestSingleAccount.short}</span>)
              <div style={{ marginTop: 2, fontSize: 'var(--fs-xs)', color: 'var(--ink-soft)' }}>
                Email: <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 600, color: 'var(--ink)' }}>{latestSingleAccount.email}</span> &nbsp;|&nbsp;
                Password: <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 600, color: 'var(--ink)', background: 'rgba(0,0,0,0.06)', padding: '1px 6px', borderRadius: 4 }}>{latestSingleAccount.password || '••••••••'}</span>
              </div>
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <button
              className="btn btn-ghost btn-sm"
              onClick={() => {
                navigator.clipboard.writeText(`Teacher: ${latestSingleAccount.name} (${latestSingleAccount.short})\nEmail: ${latestSingleAccount.email}\nPassword: ${latestSingleAccount.password || ''}`)
                setCopiedAccounts(true)
                setTimeout(() => setCopiedAccounts(false), 2000)
              }}
            >
              {copiedAccounts ? 'Copied!' : 'Copy Credentials'}
            </button>
            <button className="btn-icon" onClick={() => setLatestSingleAccount(null)}>×</button>
          </div>
        </div>
      )}

      {/* Bulk Generated Accounts Alert */}
      {createdAccounts.length > 0 && (
        <div style={{
          background: 'rgba(59, 130, 246, 0.08)',
          border: '1px solid #3b82f6',
          borderRadius: 'var(--radius)',
          padding: '10px 16px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 12,
          fontSize: 'var(--fs-sm)',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <KeyRound size={18} style={{ color: '#3b82f6', flexShrink: 0 }} />
            <span>
              <strong>{createdAccounts.length} Teacher Account(s) Generated</strong> with institutional emails and auto-generated passwords.
            </span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <button
              className="btn btn-sm btn-primary"
              style={{ background: '#3b82f6', borderColor: '#3b82f6' }}
              onClick={() => setShowAccountsModal(true)}
            >
              View Generated Credentials
            </button>
            <button className="btn-icon" onClick={() => setCreatedAccounts([])}>×</button>
          </div>
        </div>
      )}

      {/* Main Tabs Navigation */}
      <div style={{ display: 'flex', alignItems: 'center', borderBottom: '1px solid var(--line)', gap: 8 }}>
        <button
          className={`nav-item ${activeTab === 'config' ? 'active' : ''}`}
          style={{ padding: '10px 16px', borderBottom: activeTab === 'config' ? '2px solid var(--accent)' : '2px solid transparent', background: 'none', borderLeft: 'none', fontWeight: activeTab === 'config' ? 600 : 400 }}
          onClick={() => setActiveTab('config')}
        >
          <Sliders size={14} /> Configuration & Entities ({board.semesters.length} Sem, {board.rooms.length} Rooms, {board.teachers.length} Faculty, {board.subjects.length} Subj)
        </button>

        <button
          className={`nav-item ${activeTab === 'results' ? 'active' : ''}`}
          style={{ padding: '10px 16px', borderBottom: activeTab === 'results' ? '2px solid var(--accent)' : '2px solid transparent', background: 'none', borderLeft: 'none', fontWeight: activeTab === 'results' ? 600 : 400 }}
          onClick={() => setActiveTab('results')}
        >
          <Zap size={14} /> Generator Telemetry & Stats {generationResult && `(${generationResult.classes.length} Classes)`}
        </button>

        <button
          className={`nav-item ${activeTab === 'matrix' ? 'active' : ''}`}
          style={{ padding: '10px 16px', borderBottom: activeTab === 'matrix' ? '2px solid var(--accent)' : '2px solid transparent', background: 'none', borderLeft: 'none', fontWeight: activeTab === 'matrix' ? 600 : 400 }}
          onClick={() => setActiveTab('matrix')}
          disabled={!generationResult}
        >
          <Calendar size={14} /> Timetable Matrix Viewer {!generationResult && '(Run First)'}
        </button>

        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
          <button className="btn btn-ghost btn-sm" onClick={handleValidate} disabled={validating}>
            {validating ? <RefreshCw size={12} className="spin" /> : <CheckCircle2 size={12} />}
            Validate Board
          </button>
        </div>
      </div>

      {/* Validation Issues Bar */}
      {validationIssues.length > 0 && (
        <div style={{
          background: 'var(--card-bg)',
          border: `1px solid ${errorIssues.length ? 'var(--violation)' : '#f59e0b'}`,
          borderRadius: 'var(--radius)',
          padding: '14px 16px',
          boxShadow: 'var(--shadow-sm)'
        }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 10, marginBottom: issuesExpanded ? 12 : 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              {errorIssues.length ? (
                <div style={{ width: 26, height: 26, borderRadius: '50%', background: 'rgba(239, 68, 68, 0.12)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--violation)' }}>
                  <AlertCircle size={16} />
                </div>
              ) : (
                <div style={{ width: 26, height: 26, borderRadius: '50%', background: 'rgba(245, 158, 11, 0.12)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#b45309' }}>
                  <AlertTriangle size={16} />
                </div>
              )}
              <div>
                <div style={{ fontWeight: 700, fontSize: 'var(--fs-sm)', color: errorIssues.length ? 'var(--violation)' : '#b45309' }}>
                  Validation Pre-flight Diagnostics ({errorIssues.length} Errors, {warningIssues.length} Warnings)
                </div>
                <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--ink-soft)' }}>
                  {errorIssues.length > 0
                    ? 'The timetable cannot be solved until physical conflicts and capacity constraints are resolved.'
                    : 'All hard physical constraints are satisfied. Extension slots or adjustments will be applied.'}
                </div>
              </div>
            </div>

            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <div style={{ display: 'flex', background: 'var(--paper)', borderRadius: 6, padding: 2, border: '1px solid var(--line)' }}>
                <button
                  className="btn btn-ghost btn-sm"
                  style={{ fontSize: 11, padding: '2px 8px', fontWeight: issueFilter === 'all' ? 700 : 400, background: issueFilter === 'all' ? 'var(--card-bg)' : 'transparent' }}
                  onClick={() => setIssueFilter('all')}
                >
                  All ({validationIssues.length})
                </button>
                <button
                  className="btn btn-ghost btn-sm"
                  style={{ fontSize: 11, padding: '2px 8px', fontWeight: issueFilter === 'error' ? 700 : 400, color: 'var(--violation)', background: issueFilter === 'error' ? 'var(--card-bg)' : 'transparent' }}
                  onClick={() => setIssueFilter('error')}
                >
                  Errors ({errorIssues.length})
                </button>
                {warningIssues.length > 0 && (
                  <button
                    className="btn btn-ghost btn-sm"
                    style={{ fontSize: 11, padding: '2px 8px', fontWeight: issueFilter === 'warning' ? 700 : 400, color: '#b45309', background: issueFilter === 'warning' ? 'var(--card-bg)' : 'transparent' }}
                    onClick={() => setIssueFilter('warning')}
                  >
                    Warnings ({warningIssues.length})
                  </button>
                )}
              </div>

              <button
                className="btn btn-ghost btn-sm"
                style={{ fontSize: 11, padding: '2px 8px' }}
                onClick={() => setIssuesExpanded(!issuesExpanded)}
              >
                {issuesExpanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                {issuesExpanded ? 'Collapse' : 'Expand'}
              </button>
            </div>
          </div>

          {issuesExpanded && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, maxHeight: 380, overflowY: 'auto', paddingRight: 4, marginTop: 10 }}>
              {validationIssues
                .filter(issue => issueFilter === 'all' || issue.level === issueFilter)
                .map((issue, idx) => {
                  const isErr = issue.level === 'error'
                  const msg = issue.message

                  // Split message by solution or action prompt if available
                  const fixIdx = msg.indexOf('(To fix:') !== -1 ? msg.indexOf('(To fix:') : msg.indexOf('Solutions:') !== -1 ? msg.indexOf('Solutions:') : -1
                  const mainText = fixIdx !== -1 ? msg.substring(0, fixIdx).trim() : msg
                  const fixText = fixIdx !== -1 ? msg.substring(fixIdx).trim().replace(/^\(/, '').replace(/\)$/, '') : null

                  return (
                    <div
                      key={idx}
                      style={{
                        padding: '10px 14px',
                        background: 'var(--paper)',
                        borderLeft: `4px solid ${isErr ? 'var(--violation)' : '#f59e0b'}`,
                        borderTop: '1px solid var(--line)',
                        borderRight: '1px solid var(--line)',
                        borderBottom: '1px solid var(--line)',
                        borderRadius: 6,
                        fontSize: 'var(--fs-xs)',
                      }}
                    >
                      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 8 }}>
                        <div style={{ flex: 1 }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
                            <span style={{
                              background: isErr ? 'rgba(239, 68, 68, 0.12)' : 'rgba(245, 158, 11, 0.12)',
                              color: isErr ? 'var(--violation)' : '#b45309',
                              padding: '1px 6px',
                              borderRadius: 3,
                              fontWeight: 700,
                              fontSize: 10,
                              fontFamily: 'var(--font-mono)'
                            }}>
                              {isErr ? 'ERROR' : 'WARNING'}
                            </span>
                            <span style={{ fontWeight: 600, color: 'var(--ink)' }}>
                              {mainText}
                            </span>
                          </div>
                          {fixText && (
                            <div style={{
                              marginTop: 6,
                              padding: '6px 10px',
                              background: isErr ? 'rgba(0, 139, 135, 0.08)' : 'rgba(245, 158, 11, 0.08)',
                              borderRadius: 4,
                              color: 'var(--ink)',
                              fontSize: 11,
                              display: 'flex',
                              alignItems: 'flex-start',
                              gap: 6
                            }}>
                              <span style={{ color: 'var(--accent)', fontWeight: 700, flexShrink: 0 }}>💡 Remedy:</span>
                              <span style={{ color: 'var(--ink)' }}>{fixText.replace(/^To fix:\s*/i, '').replace(/^Solutions:\s*/i, '')}</span>
                            </div>
                          )}
                        </div>
                      </div>
                    </div>
                  )
                })}
            </div>
          )}
        </div>
      )}

      {/* TAB 1: CONFIGURATION & ENTITIES */}
      {activeTab === 'config' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-4)' }}>
          {/* File Upload Callout Banner */}
          <div style={{
            background: 'linear-gradient(135deg, rgba(0, 139, 135, 0.06) 0%, rgba(245, 158, 11, 0.06) 100%)',
            border: '1px solid var(--line)',
            borderRadius: 'var(--radius)',
            padding: '14px 18px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 16,
            flexWrap: 'wrap'
          }}>
            <div>
              <div style={{ fontWeight: 600, color: 'var(--ink)', display: 'flex', alignItems: 'center', gap: 6 }}>
                <Upload size={16} style={{ color: 'var(--accent)' }} />
                User File Ingestion (B.Tech, M.Tech, M.Sc & Faculty Preferences)
              </div>
              <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--ink-soft)', marginTop: 3 }}>
                Upload File 1 (Subject allocations across all active semesters) and File 2 (Faculty 3 preferred slots: 10-12:00, 12-2, 2:30-4:30 with blackboard/projector preferences).
              </div>
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <button className="btn btn-sm btn-primary" onClick={() => setShowImportModal(true)} style={{ gap: 6 }}>
                <Upload size={13} /> Upload / Extract Files
              </button>
            </div>
          </div>

          {/* Solver Settings Row */}
          <div className="card" style={{ padding: 'var(--sp-4)', background: 'var(--card-bg)', border: '1px solid var(--line)', borderRadius: 'var(--radius)' }}>
            <h3 style={{ fontFamily: 'var(--font-display)', fontSize: 'var(--fs-sm)', fontWeight: 600, color: 'var(--ink)', marginBottom: 12, display: 'flex', alignItems: 'center', gap: 6 }}>
              <Sliders size={14} /> Solver Parameters
            </h3>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 16 }}>
              <div>
                <label className="mono-label" style={{ display: 'block', marginBottom: 4 }}>Time Limit (Seconds)</label>
                <input
                  type="number"
                  step="0.5"
                  min="0.5"
                  max="60"
                  className="form-control"
                  value={board.config.time_limit_s}
                  onChange={e => {
                    const val = parseFloat(e.target.value) || 6.0
                    setBoard({ ...board, config: { ...board.config, time_limit_s: val } })
                  }}
                />
              </div>

              <div>
                <label className="mono-label" style={{ display: 'block', marginBottom: 4 }}>Random Seed</label>
                <input
                  type="number"
                  className="form-control"
                  value={board.config.seed ?? ''}
                  placeholder="Random"
                  onChange={e => {
                    const val = e.target.value ? parseInt(e.target.value, 10) : null
                    setBoard({ ...board, config: { ...board.config, seed: val } })
                  }}
                />
              </div>

              <div>
                <label className="mono-label" style={{ display: 'block', marginBottom: 4 }}>Working Days</label>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                  {['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].map(day => {
                    const active = board.config.working_days.includes(day)
                    return (
                      <button
                        key={day}
                        type="button"
                        className={`badge ${active ? 'badge-yellow' : ''}`}
                        style={{ cursor: 'pointer', border: '1px solid var(--line)' }}
                        onClick={() => {
                          const nextDays = active
                            ? board.config.working_days.filter(d => d !== day)
                            : [...board.config.working_days, day]
                          setBoard({ ...board, config: { ...board.config, working_days: nextDays } })
                        }}
                      >
                        {day}
                      </button>
                    )
                  })}
                </div>
              </div>

              <div>
                <label className="mono-label" style={{ display: 'block', marginBottom: 4 }}>1-Hour Extension Slot</label>
                <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', marginTop: 6, fontSize: 'var(--fs-sm)' }}>
                  <input
                    type="checkbox"
                    checked={board.config.allow_extension_slots}
                    onChange={e => setBoard({ ...board, config: { ...board.config, allow_extension_slots: e.target.checked } })}
                  />
                  <span>Ext 1h (16:30–17:30)</span>
                </label>
                <div style={{ fontSize: 10, color: 'var(--ink-soft)', marginTop: 2 }}>
                  Dedicated 1-hour slot for 0.5-session remainders & split classes.
                </div>
              </div>

              <div>
                <label className="mono-label" style={{ display: 'block', marginBottom: 4 }}>Split Sessions Fallback</label>
                <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', marginTop: 6, fontSize: 'var(--fs-sm)' }}>
                  <input
                    type="checkbox"
                    checked={board.config.allow_split_sessions ?? true}
                    onChange={e => setBoard({ ...board, config: { ...board.config, allow_split_sessions: e.target.checked } })}
                  />
                  <span>Split 2h to 1h+1h</span>
                </label>
                <div style={{ fontSize: 10, color: 'var(--ink-soft)', marginTop: 2 }}>
                  If regular slots are full, deliver 2h classes as two 1h classes on separate days.
                </div>
              </div>

              <div>
                <label className="mono-label" style={{ display: 'block', marginBottom: 4 }}>1h in Regular Slot</label>
                <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', marginTop: 6, fontSize: 'var(--fs-sm)' }}>
                  <input
                    type="checkbox"
                    checked={board.config.allow_short_in_regular_slot ?? false}
                    onChange={e => setBoard({ ...board, config: { ...board.config, allow_short_in_regular_slot: e.target.checked } })}
                  />
                  <span>Allow 1h in regular slot</span>
                </label>
                <div style={{ fontSize: 10, color: 'var(--ink-soft)', marginTop: 2 }}>
                  Last resort: allow a 1-hour class to occupy a 2-hour regular slot (penalized).
                </div>
              </div>
            </div>
          </div>

          {/* Semesters & Rooms Grid */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(360px, 1fr))', gap: 'var(--sp-4)' }}>
            {/* Semesters Card */}
            <div className="card" style={{ padding: 'var(--sp-4)', background: 'var(--card-bg)', border: '1px solid var(--line)', borderRadius: 'var(--radius)' }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
                <h3 style={{ fontFamily: 'var(--font-display)', fontSize: 'var(--fs-sm)', fontWeight: 600, color: 'var(--ink)', margin: 0, display: 'flex', alignItems: 'center', gap: 6 }}>
                  <Calendar size={14} /> Semesters ({board.semesters.length})
                </h3>
                <button
                  className="btn btn-sm btn-ghost"
                  style={{ gap: 4, color: 'var(--accent)' }}
                  onClick={() => setShowAddSemesterModal(true)}
                >
                  <Plus size={13} /> Add Semester
                </button>
              </div>
              <div style={{ maxHeight: 240, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 6 }}>
                {board.semesters.length === 0 ? (
                  <div style={{ padding: 24, textAlign: 'center', color: 'var(--ink-soft)', fontSize: 'var(--fs-xs)', border: '1px dashed var(--line)', borderRadius: 6 }}>
                    No semesters added yet. Click "+ Add Semester" or upload curriculum files.
                  </div>
                ) : (
                  board.semesters.map((s, idx) => (
                    <div key={idx} style={{ padding: '6px 10px', background: 'var(--paper)', border: '1px solid var(--line)', borderRadius: 6, display: 'flex', alignItems: 'center', justifyContent: 'space-between', fontSize: 'var(--fs-xs)' }}>
                      <div>
                        <strong style={{ color: 'var(--ink)' }}>{s.id}</strong> · {s.programme} Sem {s.number} {s.name ? `(${s.name})` : ''}
                        {s.strength ? <span style={{ color: 'var(--ink-soft)', marginLeft: 6 }}>({s.strength} students)</span> : null}
                      </div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                        <button
                          className="btn-icon"
                          style={{ color: 'var(--accent)', width: 22, height: 22 }}
                          onClick={() => {
                            setEditingSemesterIdx(idx)
                            setEditingSemester(JSON.parse(JSON.stringify(s)))
                          }}
                          title="Edit semester"
                        >
                          <Pencil size={12} />
                        </button>
                        <button
                          className="btn-icon"
                          style={{ color: 'var(--violation)', width: 22, height: 22 }}
                          onClick={() => {
                            const updated = { ...board, semesters: board.semesters.filter((_, i) => i !== idx) }
                            setBoard(updated)
                            autoValidateBoard(updated)
                          }}
                          title="Remove semester"
                        >
                          <Trash2 size={12} />
                        </button>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>

            {/* Rooms Card */}
            <div className="card" style={{ padding: 'var(--sp-4)', background: 'var(--card-bg)', border: '1px solid var(--line)', borderRadius: 'var(--radius)' }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
                <h3 style={{ fontFamily: 'var(--font-display)', fontSize: 'var(--fs-sm)', fontWeight: 600, color: 'var(--ink)', margin: 0, display: 'flex', alignItems: 'center', gap: 6 }}>
                  <MapPin size={14} /> Rooms & Labs ({board.rooms.length})
                </h3>
                <button
                  className="btn btn-sm btn-ghost"
                  style={{ gap: 4, color: 'var(--accent)' }}
                  onClick={() => setShowAddRoomModal(true)}
                >
                  <Plus size={13} /> Add Room
                </button>
              </div>
              <div style={{ maxHeight: 240, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 6 }}>
                {board.rooms.length === 0 ? (
                  <div style={{ padding: 24, textAlign: 'center', color: 'var(--ink-soft)', fontSize: 'var(--fs-xs)', border: '1px dashed var(--line)', borderRadius: 6 }}>
                    No rooms configured. Click "+ Add Room" to add lecture halls or labs.
                  </div>
                ) : (
                  board.rooms.map((r, idx) => (
                    <div key={idx} style={{ padding: '6px 10px', background: 'var(--paper)', border: '1px solid var(--line)', borderRadius: 6, display: 'flex', alignItems: 'center', justifyContent: 'space-between', fontSize: 'var(--fs-xs)' }}>
                      <div>
                        <strong style={{ color: 'var(--ink)' }}>{r.number}</strong> · <span className="badge">{r.kind}</span> · {r.facility} {r.capacity ? <span style={{ color: 'var(--ink-soft)' }}>(Cap: {r.capacity})</span> : null}
                      </div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                        <button
                          className="btn-icon"
                          style={{ color: 'var(--accent)', width: 22, height: 22 }}
                          onClick={() => {
                            setEditingRoomIdx(idx)
                            setEditingRoom(JSON.parse(JSON.stringify(r)))
                          }}
                          title="Edit room"
                        >
                          <Pencil size={12} />
                        </button>
                        <button
                          className="btn-icon"
                          style={{ color: 'var(--violation)', width: 22, height: 22 }}
                          onClick={() => {
                            const updated = { ...board, rooms: board.rooms.filter((_, i) => i !== idx) }
                            setBoard(updated)
                            autoValidateBoard(updated)
                          }}
                          title="Remove room"
                        >
                          <Trash2 size={12} />
                        </button>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>

          {/* Teachers Section */}
          <div className="card" style={{ padding: 'var(--sp-4)', background: 'var(--card-bg)', border: '1px solid var(--line)', borderRadius: 'var(--radius)' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
              <h3 style={{ fontFamily: 'var(--font-display)', fontSize: 'var(--fs-sm)', fontWeight: 600, color: 'var(--ink)', margin: 0, display: 'flex', alignItems: 'center', gap: 6 }}>
                <Users size={14} /> Faculty & Preferences ({board.teachers.length})
              </h3>
              <button
                className="btn btn-sm btn-ghost"
                style={{ gap: 4, color: 'var(--accent)' }}
                onClick={() => setShowAddTeacherModal(true)}
              >
                <Plus size={13} /> Add Faculty
              </button>
            </div>
            <div style={{ maxHeight: 280, overflowY: 'auto', display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 8 }}>
              {board.teachers.length === 0 ? (
                <div style={{ gridColumn: '1 / -1', padding: 32, textAlign: 'center', color: 'var(--ink-soft)', fontSize: 'var(--fs-xs)', border: '1px dashed var(--line)', borderRadius: 6 }}>
                  No faculty members added yet. Click "+ Add Faculty" or import Teacher Preferences File.
                </div>
              ) : (
                board.teachers.map((t, idx) => (
                  <div key={idx} style={{ padding: '10px 12px', background: 'var(--paper)', border: '1px solid var(--line)', borderRadius: 6, fontSize: 'var(--fs-xs)', position: 'relative' }}>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                      <div style={{ fontWeight: 600, color: 'var(--ink)' }}>{t.name} <span style={{ color: 'var(--ink-soft)', fontFamily: 'var(--font-mono)' }}>({t.id})</span></div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                        <span className={`badge ${t.internal ? 'badge-green' : 'badge-yellow'}`} style={{ fontSize: 9 }}>
                          {t.internal ? 'INTERNAL' : 'EXTERNAL'}
                        </span>
                        <button
                          className="btn-icon"
                          style={{ color: 'var(--accent)', width: 20, height: 20 }}
                          onClick={() => {
                            setEditingTeacherIdx(idx)
                            setEditingTeacher(JSON.parse(JSON.stringify(t)))
                          }}
                          title="Edit faculty member & preferences"
                        >
                          <Pencil size={11} />
                        </button>
                        <button
                          className="btn-icon"
                          style={{ color: 'var(--violation)', width: 20, height: 20 }}
                          onClick={() => {
                            const updated = { ...board, teachers: board.teachers.filter((_, i) => i !== idx) }
                            setBoard(updated)
                            autoValidateBoard(updated)
                          }}
                          title="Remove faculty"
                        >
                          <Trash2 size={11} />
                        </button>
                      </div>
                    </div>
                    {t.preferences.length > 0 && (
                      <div style={{ marginTop: 6, display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                        {t.preferences.map((p, pIdx) => (
                          <span key={pIdx} style={{ background: 'rgba(0, 139, 135, 0.1)', color: 'var(--accent)', padding: '1px 5px', borderRadius: 3, fontSize: 10 }}>
                            #{pIdx + 1} {p.day} S{p.slot} ({p.facility})
                          </span>
                        ))}
                      </div>
                    )}
                    {t.unavailable_days.length > 0 && (
                      <div style={{ marginTop: 4, color: 'var(--violation)', fontSize: 10 }}>
                        Unavailable: {t.unavailable_days.join(', ')}
                      </div>
                    )}
                  </div>
                ))
              )}
            </div>
          </div>

          {/* Subjects Section */}
          <div className="card" style={{ padding: 'var(--sp-4)', background: 'var(--card-bg)', border: '1px solid var(--line)', borderRadius: 'var(--radius)' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
              <h3 style={{ fontFamily: 'var(--font-display)', fontSize: 'var(--fs-sm)', fontWeight: 600, color: 'var(--ink)', margin: 0, display: 'flex', alignItems: 'center', gap: 6 }}>
                <BookOpen size={14} /> Course Subjects & Allocations ({board.subjects.length})
              </h3>
              <button
                className="btn btn-sm btn-ghost"
                style={{ gap: 4, color: 'var(--accent)' }}
                onClick={() => {
                  if (board.semesters.length > 0 && !newSubject.semester_id) {
                    setNewSubject(prev => ({ ...prev, semester_id: board.semesters[0].id }))
                  }
                  setShowAddSubjectModal(true)
                }}
              >
                <Plus size={13} /> Add Subject
              </button>
            </div>
            <div style={{ maxHeight: 300, overflowY: 'auto', display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 8 }}>
              {board.subjects.length === 0 ? (
                <div style={{ gridColumn: '1 / -1', padding: 32, textAlign: 'center', color: 'var(--ink-soft)', fontSize: 'var(--fs-xs)', border: '1px dashed var(--line)', borderRadius: 6 }}>
                  No course subjects allocated yet. Click "+ Add Subject" or import Subject Allocations File.
                </div>
              ) : (
                board.subjects.map((sub, idx) => {
                  const teacherId = sub.internal_teacher_id || sub.external_teacher_id
                  const teacherObj = teacherId ? board.teachers.find(t => t.id === teacherId) : null
                  const teacherLabel = teacherObj ? `${teacherObj.name} (${teacherId})` : (teacherId || 'TBD')

                  return (
                    <div key={idx} style={{ padding: '8px 12px', background: 'var(--paper)', border: '1px solid var(--line)', borderRadius: 6, fontSize: 'var(--fs-xs)' }}>
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                        <strong style={{ color: 'var(--ink)' }}>{sub.code}</strong>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                          <span className={`badge ${sub.class_type === 'practical' ? 'badge-yellow' : ''}`} style={{ fontSize: 9 }}>
                            {sub.class_type.toUpperCase()}
                          </span>
                          <button
                            className="btn-icon"
                            style={{ color: 'var(--accent)', width: 20, height: 20 }}
                            onClick={() => {
                              setEditingSubjectIdx(idx)
                              setEditingSubject(JSON.parse(JSON.stringify(sub)))
                            }}
                            title="Edit course subject & allocations"
                          >
                            <Pencil size={11} />
                          </button>
                          <button
                            className="btn-icon"
                            style={{ color: 'var(--violation)', width: 20, height: 20 }}
                            onClick={() => {
                              const updated = { ...board, subjects: board.subjects.filter((_, i) => i !== idx) }
                              setBoard(updated)
                              autoValidateBoard(updated)
                            }}
                            title="Remove subject"
                          >
                            <Trash2 size={11} />
                          </button>
                        </div>
                      </div>
                      <div style={{ color: 'var(--ink)', marginTop: 2, fontWeight: 500 }}>{sub.name}</div>
                      <div style={{ color: 'var(--ink-soft)', marginTop: 4, display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
                        <span>Sem: <strong>{sub.semester_id}</strong></span>
                        <span title={teacherLabel} style={{ maxWidth: 160, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          Teacher: <strong style={{ color: 'var(--ink)' }}>{teacherLabel}</strong>
                        </span>
                      </div>
                      <div style={{ color: 'var(--ink-soft)', marginTop: 2, display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, fontSize: 11 }}>
                        <span>Sessions: <strong>{sub.sessions_per_week ?? (sub.class_type === 'practical' ? 1 : 3)}/wk</strong> ({((sub.sessions_per_week ?? (sub.class_type === 'practical' ? 1 : 3)) * 2)} hrs)</span>
                        {sub.external_sessions ? <span>Ext: <strong>{sub.external_sessions}</strong></span> : null}
                      </div>
                    </div>
                  )
                })
              )}
            </div>
          </div>
        </div>
      )}

      {/* TAB 2: RUN & TELEMETRY */}
      {activeTab === 'results' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-4)' }}>
          {generationResult ? (
            <>
              {/* Executive Stat Cards */}
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 12 }}>
                <div className="card" style={{ padding: '16px', background: 'var(--card-bg)', border: '1px solid var(--line)', borderRadius: 'var(--radius)' }}>
                  <div className="mono-label" style={{ color: 'var(--ink-soft)' }}>Classes Scheduled</div>
                  <div style={{ fontSize: 'var(--fs-xl)', fontWeight: 700, color: 'var(--ink)', marginTop: 4 }}>
                    {generationResult.classes.length}
                    <span style={{ fontSize: 'var(--fs-sm)', fontWeight: 400, color: 'var(--ink-soft)', marginLeft: 4 }}>
                      ({generationResult.unscheduled.length} unscheduled)
                    </span>
                  </div>
                </div>

                <div className="card" style={{ padding: '16px', background: 'var(--card-bg)', border: '1px solid var(--line)', borderRadius: 'var(--radius)' }}>
                  <div className="mono-label" style={{ color: 'var(--ink-soft)' }}>Hard Violations</div>
                  <div style={{ fontSize: 'var(--fs-xl)', fontWeight: 700, color: generationResult.violations.length === 0 ? '#10b981' : 'var(--violation)', marginTop: 4, display: 'flex', alignItems: 'center', gap: 6 }}>
                    {generationResult.violations.length}
                    {generationResult.violations.length === 0 && <CheckCircle2 size={18} style={{ color: '#10b981' }} />}
                  </div>
                </div>

                <div className="card" style={{ padding: '16px', background: 'var(--card-bg)', border: '1px solid var(--line)', borderRadius: 'var(--radius)' }}>
                  <div className="mono-label" style={{ color: 'var(--ink-soft)' }}>Preferences Honoured</div>
                  <div style={{ fontSize: 'var(--fs-xl)', fontWeight: 700, color: 'var(--accent)', marginTop: 4 }}>
                    {generationResult.preferences?._overall?.percent ?? 0}%
                    <span style={{ fontSize: 'var(--fs-xs)', fontWeight: 400, color: 'var(--ink-soft)', marginLeft: 4 }}>
                      ({generationResult.preferences?._overall?.honoured ?? 0}/{generationResult.preferences?._overall?.achievable ?? 0})
                    </span>
                  </div>
                </div>

                <div className="card" style={{ padding: '16px', background: 'var(--card-bg)', border: '1px solid var(--line)', borderRadius: 'var(--radius)' }}>
                  <div className="mono-label" style={{ color: 'var(--ink-soft)' }}>Extension Slot (1h)</div>
                  <div style={{ fontSize: 'var(--fs-xl)', fontWeight: 700, color: 'var(--ink)', marginTop: 4 }}>
                    {generationResult.stats?.extension_slot_classes ?? 0}
                  </div>
                </div>

                <div className="card" style={{ padding: '16px', background: 'var(--card-bg)', border: '1px solid var(--line)', borderRadius: 'var(--radius)' }}>
                  <div className="mono-label" style={{ color: 'var(--ink-soft)' }}>1-Hour / Split Classes</div>
                  <div style={{ fontSize: 'var(--fs-xl)', fontWeight: 700, color: 'var(--ink)', marginTop: 4 }}>
                    {generationResult.stats?.half_session_classes ?? 0}
                    <span style={{ fontSize: 'var(--fs-xs)', fontWeight: 400, color: 'var(--ink-soft)', marginLeft: 4 }}>
                      ({generationResult.stats?.split_piece_classes ?? 0} split)
                    </span>
                  </div>
                </div>

                <div className="card" style={{ padding: '16px', background: 'var(--card-bg)', border: '1px solid var(--line)', borderRadius: 'var(--radius)' }}>
                  <div className="mono-label" style={{ color: 'var(--ink-soft)' }}>Runtime & Loops</div>
                  <div style={{ fontSize: 'var(--fs-xl)', fontWeight: 700, color: 'var(--ink)', marginTop: 4 }}>
                    {generationResult.stats?.runtime_s ?? 0}s
                    <span style={{ fontSize: 'var(--fs-xs)', fontWeight: 400, color: 'var(--ink-soft)', marginLeft: 4 }}>
                      ({generationResult.stats?.sa_iterations ?? 0} SA)
                    </span>
                  </div>
                </div>
              </div>

              {/* Action Toolbar */}
              <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 10, background: 'var(--card-bg)', padding: 12, borderRadius: 'var(--radius)', border: '1px solid var(--line)' }}>
                <button
                  className="btn btn-primary"
                  style={{ background: 'var(--accent)', color: '#fff', gap: 6 }}
                  onClick={() => handleSaveDraft(false)}
                  disabled={savingDraft}
                >
                  {savingDraft ? <RefreshCw size={13} className="spin" /> : <Save size={13} />}
                  {savingDraft ? 'Saving to Database…' : 'Save as New Draft Version'}
                </button>

                <button
                  className="btn btn-success"
                  onClick={() => handleSaveDraft(true)}
                  disabled={savingDraft}
                >
                  🚀 Save & Publish Immediately
                </button>

                <button className="btn btn-ghost" onClick={handleDownloadCSV} style={{ gap: 6 }}>
                  <Download size={13} /> Export CSV
                </button>

                <button className="btn btn-ghost" onClick={handleDownloadJSON} style={{ gap: 6 }}>
                  <Copy size={13} /> Export JSON
                </button>

                <button className="btn btn-ghost" style={{ marginLeft: 'auto' }} onClick={() => setActiveTab('matrix')}>
                  View Interactive Matrix →
                </button>
              </div>

              {/* Unscheduled Classes Detailed Diagnostic Inspector */}
              {generationResult.unscheduled.length > 0 && (() => {
                const uniqueSemesters = Array.from(new Set(generationResult.unscheduled.map(u => u.semester)))
                const filteredUnscheduled = generationResult.unscheduled.filter(u => {
                  if (unscheduledFilterSem !== 'ALL' && u.semester !== unscheduledFilterSem) return false
                  if (unscheduledSearch.trim()) {
                    const q = unscheduledSearch.toLowerCase().trim()
                    const matchSubj = u.subject.toLowerCase().includes(q) || (u.subject_name && u.subject_name.toLowerCase().includes(q))
                    const matchTch = (u.teacher_name && u.teacher_name.toLowerCase().includes(q)) || u.teacher.toLowerCase().includes(q)
                    const matchSem = u.semester.toLowerCase().includes(q) || (u.semester_name && u.semester_name.toLowerCase().includes(q))
                    const matchReason = u.reason.toLowerCase().includes(q)
                    return matchSubj || matchTch || matchSem || matchReason
                  }
                  return true
                })

                return (
                  <div style={{
                    background: 'rgba(239, 68, 68, 0.04)',
                    border: '1px solid rgba(239, 68, 68, 0.35)',
                    borderRadius: 'var(--radius)',
                    padding: 'var(--sp-4)',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: 14
                  }}>
                    {/* Header Banner */}
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 12 }}>
                      <div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                          <AlertTriangle size={18} style={{ color: 'var(--violation)' }} />
                          <h3 style={{ margin: 0, fontSize: 'var(--fs-base)', fontWeight: 700, color: 'var(--violation)' }}>
                            Unscheduled Classes ({generationResult.unscheduled.length})
                          </h3>
                        </div>
                        <p style={{ margin: '4px 0 0', fontSize: 'var(--fs-xs)', color: 'var(--ink-soft)', maxWidth: 720 }}>
                          The optimizer could not place these {generationResult.unscheduled.length} sessions without violating hard capacity rules (such as cohort simultaneous sessions, teacher max-1-theory/day, or physical room availability). Inspect the exact slot conflicts and remedies below:
                        </p>
                      </div>

                      {board && !board.config.allow_extension_slots && (
                        <button
                          className="btn btn-sm btn-primary"
                          style={{ gap: 6, alignSelf: 'center' }}
                          onClick={() => {
                            const nextBoard: GeneratorBoard = {
                              ...board,
                              config: { ...board.config, allow_extension_slots: true }
                            }
                            setBoard(nextBoard)
                            autoValidateBoard(nextBoard)
                            setSaveSuccessMsg('Enabled extension slots (Tier 1: 08:00–10:00 & 16:30–18:30). Click "Run Generator Now" to re-schedule.')
                          }}
                        >
                          <Zap size={13} /> Enable Extension Slots
                        </button>
                      )}
                    </div>

                    {/* Filter & Search Bar */}
                    <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', borderTop: '1px solid rgba(239, 68, 68, 0.15)', paddingTop: 10 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 'var(--fs-xs)', color: 'var(--ink-soft)' }}>
                        <Filter size={13} /> Cohort Filter:
                      </div>
                      <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                        <button
                          className={`btn btn-xs ${unscheduledFilterSem === 'ALL' ? 'btn-primary' : 'btn-ghost'}`}
                          onClick={() => setUnscheduledFilterSem('ALL')}
                        >
                          All ({generationResult.unscheduled.length})
                        </button>
                        {uniqueSemesters.map(semId => {
                          const count = generationResult.unscheduled.filter(u => u.semester === semId).length
                          const semObj = board?.semesters.find(s => s.id === semId)
                          const label = semObj ? `${semObj.programme} Sem ${semObj.number}` : semId
                          return (
                            <button
                              key={semId}
                              className={`btn btn-xs ${unscheduledFilterSem === semId ? 'btn-primary' : 'btn-ghost'}`}
                              onClick={() => setUnscheduledFilterSem(semId)}
                            >
                              {label} ({count})
                            </button>
                          )
                        })}
                      </div>

                      <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 6 }}>
                        <div style={{ position: 'relative' }}>
                          <Search size={12} style={{ position: 'absolute', left: 8, top: '50%', transform: 'translateY(-50%)', color: 'var(--ink-soft)' }} />
                          <input
                            type="text"
                            placeholder="Filter unscheduled..."
                            value={unscheduledSearch}
                            onChange={e => setUnscheduledSearch(e.target.value)}
                            style={{
                              fontSize: 11,
                              padding: '4px 8px 4px 26px',
                              borderRadius: 'var(--radius)',
                              border: '1px solid var(--line)',
                              background: 'var(--card-bg)',
                              color: 'var(--ink)',
                              width: 180,
                            }}
                          />
                        </div>
                        {unscheduledSearch && (
                          <button
                            className="btn btn-xs btn-ghost"
                            onClick={() => setUnscheduledSearch('')}
                            style={{ padding: '2px 6px' }}
                          >
                            <X size={11} />
                          </button>
                        )}
                      </div>
                    </div>

                    {/* Unscheduled Class Cards List */}
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                      {filteredUnscheduled.map((u, i) => {
                        const isExpanded = !!expandedUnscheduledIdxs[i]
                        const semObj = board?.semesters.find(s => s.id === u.semester)
                        const semTitle = u.semester_name || (semObj ? `${semObj.programme} Sem ${semObj.number} (${semObj.id})` : u.semester)
                        const teacherObj = board?.teachers.find(t => t.id === u.teacher)
                        const teacherTitle = u.teacher_label || (teacherObj ? `${teacherObj.name} (${teacherObj.id})` : `Teacher ${u.teacher}`)
                        const subjectObj = board?.subjects.find(s => s.code === u.subject)
                        const subjectTitle = u.subject_name || subjectObj?.name || u.subject

                        return (
                          <div
                            key={i}
                            className="card"
                            style={{
                              background: 'var(--card-bg)',
                              border: '1px solid rgba(239, 68, 68, 0.25)',
                              borderRadius: 'var(--radius)',
                              padding: '12px 14px',
                              display: 'flex',
                              flexDirection: 'column',
                              gap: 8,
                            }}
                          >
                            {/* Card Top Row: Subject & Action Buttons */}
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 8 }}>
                              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                                <span style={{
                                  fontFamily: 'var(--font-mono)',
                                  fontWeight: 700,
                                  fontSize: 'var(--fs-sm)',
                                  color: 'var(--violation)',
                                  background: 'rgba(239, 68, 68, 0.1)',
                                  padding: '2px 8px',
                                  borderRadius: 4,
                                }}>
                                  {u.subject}
                                </span>
                                <span style={{ fontWeight: 600, fontSize: 'var(--fs-sm)', color: 'var(--ink)' }}>
                                  {subjectTitle}
                                </span>
                                <span className={`badge ${u.class_type === 'practical' ? 'badge-blue' : 'badge-green'}`} style={{ fontSize: 10 }}>
                                  {u.class_type ? u.class_type.toUpperCase() : 'THEORY'}
                                </span>
                                <span style={{
                                  fontSize: 11,
                                  background: 'var(--bg-subtle)',
                                  padding: '2px 6px',
                                  borderRadius: 4,
                                  color: 'var(--ink-soft)',
                                }}>
                                  Cohort: <strong style={{ color: 'var(--ink)' }}>{semTitle}</strong>
                                </span>
                                <span style={{
                                  fontSize: 11,
                                  background: 'var(--bg-subtle)',
                                  padding: '2px 6px',
                                  borderRadius: 4,
                                  color: 'var(--ink-soft)',
                                }}>
                                  Faculty: <strong style={{ color: 'var(--ink)' }}>{teacherTitle}</strong>
                                </span>
                              </div>

                              <div style={{ display: 'flex', gap: 6 }}>
                                <button
                                  className="btn btn-xs btn-ghost"
                                  onClick={() => openEditSubjectByCode(u.subject)}
                                  title="Edit course allocations or reduce sessions"
                                  style={{ gap: 4 }}
                                >
                                  <Pencil size={11} /> Edit Course
                                </button>
                                <button
                                  className="btn btn-xs btn-ghost"
                                  onClick={() => openEditTeacherById(u.teacher)}
                                  title="Edit faculty member preferences or type"
                                  style={{ gap: 4 }}
                                >
                                  <Pencil size={11} /> Edit Faculty
                                </button>
                              </div>
                            </div>

                            {/* Specific Conflict Breakdown Badges */}
                            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, fontSize: 11 }}>
                              {u.category_slots?.semester_busy && u.category_slots.semester_busy.length > 0 && (
                                <div style={{
                                  background: 'rgba(239, 68, 68, 0.08)',
                                  border: '1px solid rgba(239, 68, 68, 0.25)',
                                  borderRadius: 4,
                                  padding: '3px 8px',
                                  color: 'var(--violation)',
                                }}>
                                  <strong>Cohort Clashing ({u.category_slots.semester_busy.length} slots):</strong>{' '}
                                  {u.category_slots.semester_busy.slice(0, 4).map(s => s.slot).join(', ')}
                                  {u.category_slots.semester_busy.length > 4 ? ` and ${u.category_slots.semester_busy.length - 4} more` : ''}
                                </div>
                              )}

                              {u.category_slots?.teacher_busy && u.category_slots.teacher_busy.length > 0 && (
                                <div style={{
                                  background: 'rgba(245, 158, 11, 0.08)',
                                  border: '1px solid rgba(245, 158, 11, 0.3)',
                                  borderRadius: 4,
                                  padding: '3px 8px',
                                  color: '#b45309',
                                }}>
                                  <strong>Faculty Teaching Other Class ({u.category_slots.teacher_busy.length} slots):</strong>{' '}
                                  {u.category_slots.teacher_busy.slice(0, 3).map(s => s.slot).join(', ')}
                                  {u.category_slots.teacher_busy.length > 3 ? ` and ${u.category_slots.teacher_busy.length - 3} more` : ''}
                                </div>
                              )}

                              {u.category_slots?.teacher_daily_limit && u.category_slots.teacher_daily_limit.length > 0 && (
                                <div style={{
                                  background: 'rgba(59, 130, 246, 0.08)',
                                  border: '1px solid rgba(59, 130, 246, 0.3)',
                                  borderRadius: 4,
                                  padding: '3px 8px',
                                  color: '#1d4ed8',
                                }}>
                                  <strong>Max 1 Theory/Day Limit:</strong> Already teaching on {u.category_slots.teacher_daily_limit.map(d => d.day).join(', ')}
                                </div>
                              )}

                              {u.category_slots?.teacher_free_days && u.category_slots.teacher_free_days.length > 0 && (
                                <div style={{
                                  background: 'rgba(168, 85, 247, 0.08)',
                                  border: '1px solid rgba(168, 85, 247, 0.3)',
                                  borderRadius: 4,
                                  padding: '3px 8px',
                                  color: '#7e22ce',
                                }}>
                                  <strong>Guaranteed Free Day:</strong> {u.category_slots.teacher_free_days.join(', ')} preserved
                                </div>
                              )}

                              {u.category_slots?.room_unavailable && u.category_slots.room_unavailable.length > 0 && (
                                <div style={{
                                  background: 'rgba(107, 114, 128, 0.08)',
                                  border: '1px solid rgba(107, 114, 128, 0.3)',
                                  borderRadius: 4,
                                  padding: '3px 8px',
                                  color: 'var(--ink-soft)',
                                }}>
                                  <strong>No Room Available ({u.category_slots.room_unavailable.length} slots)</strong>
                                </div>
                              )}
                            </div>

                            {/* Actionable Remedy Banner */}
                            {u.remedy && (
                              <div style={{
                                background: 'rgba(59, 130, 246, 0.06)',
                                borderLeft: '3px solid #3b82f6',
                                borderRadius: '0 4px 4px 0',
                                padding: '6px 10px',
                                fontSize: 'var(--fs-xs)',
                                color: 'var(--ink)',
                                display: 'flex',
                                alignItems: 'center',
                                justifyContent: 'space-between',
                                gap: 8,
                                flexWrap: 'wrap',
                              }}>
                                <div>
                                  <strong style={{ color: '#1d4ed8' }}>💡 Remedy:</strong> {u.remedy}
                                </div>
                              </div>
                            )}

                            {/* Expand/Collapse Slot Conflicts Inspection */}
                            {u.slot_conflicts && u.slot_conflicts.length > 0 && (
                              <div>
                                <button
                                  className="btn btn-xs btn-ghost"
                                  onClick={() => setExpandedUnscheduledIdxs(prev => ({ ...prev, [i]: !prev[i] }))}
                                  style={{ gap: 4, color: 'var(--ink-soft)' }}
                                >
                                  {isExpanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
                                  {isExpanded ? 'Hide All Slot Conflicts' : `Inspect All ${u.slot_conflicts.length} Evaluated Slots & Blockers`}
                                </button>

                                {isExpanded && (
                                  <div style={{ marginTop: 8, overflowX: 'auto', border: '1px solid var(--line)', borderRadius: 'var(--radius)' }}>
                                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
                                      <thead>
                                        <tr style={{ background: 'var(--bg-subtle)', textAlign: 'left', borderBottom: '1px solid var(--line)' }}>
                                          <th style={{ padding: '6px 10px', width: 60 }}>Day</th>
                                          <th style={{ padding: '6px 10px', width: 140 }}>Slot / Time</th>
                                          <th style={{ padding: '6px 10px', width: 160 }}>Constraint Blocker</th>
                                          <th style={{ padding: '6px 10px' }}>Conflict Explanation</th>
                                        </tr>
                                      </thead>
                                      <tbody>
                                        {u.slot_conflicts.map((sc, scIdx) => {
                                          let badgeBg = 'var(--bg-subtle)'
                                          let badgeColor = 'var(--ink)'
                                          let blockerTitle = sc.conflict_type
                                          if (sc.conflict_type === 'semester_busy') {
                                            badgeBg = 'rgba(239, 68, 68, 0.1)'
                                            badgeColor = 'var(--violation)'
                                            blockerTitle = 'Cohort Attending Class'
                                          } else if (sc.conflict_type === 'teacher_busy') {
                                            badgeBg = 'rgba(245, 158, 11, 0.1)'
                                            badgeColor = '#b45309'
                                            blockerTitle = 'Faculty Teaching Other Class'
                                          } else if (sc.conflict_type === 'teacher_daily_limit') {
                                            badgeBg = 'rgba(59, 130, 246, 0.1)'
                                            badgeColor = '#1d4ed8'
                                            blockerTitle = 'Max 1 Theory/Day Reached'
                                          } else if (sc.conflict_type === 'teacher_free_day') {
                                            badgeBg = 'rgba(168, 85, 247, 0.1)'
                                            badgeColor = '#7e22ce'
                                            blockerTitle = 'Protected Weekly Free Day'
                                          } else if (sc.conflict_type === 'room_unavailable') {
                                            badgeBg = 'rgba(107, 114, 128, 0.1)'
                                            badgeColor = 'var(--ink-soft)'
                                            blockerTitle = 'No Room Available'
                                          } else if (sc.conflict_type === 'teacher_unavailable') {
                                            badgeBg = 'rgba(239, 68, 68, 0.1)'
                                            badgeColor = 'var(--violation)'
                                            blockerTitle = 'Faculty Marked Unavailable'
                                          }

                                          return (
                                            <tr key={scIdx} style={{ borderBottom: '1px solid var(--line)', background: scIdx % 2 === 0 ? 'var(--card-bg)' : 'var(--bg-subtle)' }}>
                                              <td style={{ padding: '6px 10px', fontWeight: 600 }}>{sc.day}</td>
                                              <td style={{ padding: '6px 10px', fontFamily: 'var(--font-mono)' }}>{sc.time_str}</td>
                                              <td style={{ padding: '6px 10px' }}>
                                                <span style={{ background: badgeBg, color: badgeColor, padding: '2px 6px', borderRadius: 3, fontWeight: 500, fontSize: 10 }}>
                                                  {blockerTitle}
                                                </span>
                                              </td>
                                              <td style={{ padding: '6px 10px', color: 'var(--ink)' }}>{sc.detail}</td>
                                            </tr>
                                          )
                                        })}
                                      </tbody>
                                    </table>
                                  </div>
                                )}
                              </div>
                            )}
                          </div>
                        )
                      })}
                    </div>
                  </div>
                )
              })()}

              {/* Summary Preformatted Box */}
              <div className="card" style={{ padding: 'var(--sp-4)', background: 'var(--card-bg)', border: '1px solid var(--line)', borderRadius: 'var(--radius)' }}>
                <h4 style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-xs)', textTransform: 'uppercase', color: 'var(--ink-soft)', marginBottom: 8 }}>
                  Console Telemetry Summary
                </h4>
                <pre style={{
                  background: 'var(--ink)', color: '#a7f3d0', padding: 16, borderRadius: 'var(--radius)',
                  fontFamily: 'var(--font-mono)', fontSize: 11, lineHeight: 1.5, overflowX: 'auto', margin: 0
                }}>
                  {generationResult.summary}
                </pre>
              </div>
            </>
          ) : (
            <div style={{ padding: 'var(--sp-8)', textAlign: 'center', background: 'var(--card-bg)', border: '1px dashed var(--line)', borderRadius: 'var(--radius)' }}>
              <Zap size={32} style={{ color: 'var(--ink-soft)', margin: '0 auto 12px' }} />
              <h3 style={{ fontFamily: 'var(--font-display)', fontSize: 'var(--fs-md)', color: 'var(--ink)' }}>No timetable generated yet</h3>
              <p style={{ fontSize: 'var(--fs-sm)', color: 'var(--ink-soft)', maxWidth: 460, margin: '6px auto 16px' }}>
                Click "Run Generator" to execute the CSP and Simulated Annealing algorithm on the loaded board.
              </p>
              <button className="btn btn-primary" onClick={handleGenerate} disabled={generating} style={{ margin: '0 auto', gap: 6 }}>
                {generating ? <RefreshCw size={13} className="spin" /> : <Play size={13} fill="currentColor" />}
                {generating ? 'Optimizing…' : 'Run Generator Now'}
              </button>
            </div>
          )}
        </div>
      )}

      {/* TAB 3: MATRIX VIEWER */}
      {activeTab === 'matrix' && generationResult && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-4)' }}>
          {/* Sub-navigation Controls */}
          <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
            <div style={{ display: 'flex', gap: 4 }}>
              <button
                className={`btn btn-sm ${matrixViewMode === 'semester' ? 'btn-primary' : 'btn-ghost'}`}
                onClick={() => setMatrixViewMode('semester')}
              >
                By Semester
              </button>
              <button
                className={`btn btn-sm ${matrixViewMode === 'teacher' ? 'btn-primary' : 'btn-ghost'}`}
                onClick={() => setMatrixViewMode('teacher')}
              >
                By Teacher & Free Days
              </button>
              <button
                className={`btn btn-sm ${matrixViewMode === 'preferences' ? 'btn-primary' : 'btn-ghost'}`}
                onClick={() => setMatrixViewMode('preferences')}
              >
                Preferences Audit
              </button>
              <button
                className={`btn btn-sm ${matrixViewMode === 'all' ? 'btn-primary' : 'btn-ghost'}`}
                onClick={() => setMatrixViewMode('all')}
              >
                All Classes Table
              </button>
            </div>

            {/* Semester or Teacher selector */}
            {matrixViewMode === 'semester' && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span className="mono-label">Semester:</span>
                <select
                  className="form-control"
                  style={{ width: 230 }}
                  value={selectedSemesterId}
                  onChange={e => setSelectedSemesterId(e.target.value)}
                >
                  <option value="ALL">All Semesters ({board.semesters.length})</option>
                  {board.semesters.map(s => (
                    <option key={s.id} value={s.id}>{s.id} ({s.programme} S{s.number})</option>
                  ))}
                </select>
              </div>
            )}

            {matrixViewMode === 'teacher' && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span className="mono-label">Teacher:</span>
                <select
                  className="form-control"
                  style={{ width: 220 }}
                  value={selectedTeacherId}
                  onChange={e => setSelectedTeacherId(e.target.value)}
                >
                  {board.teachers.map(t => (
                    <option key={t.id} value={t.id}>{t.name} ({t.id})</option>
                  ))}
                </select>
              </div>
            )}
          </div>

          {/* SUB-VIEW 1: SEMESTER GRID */}
          {matrixViewMode === 'semester' && (
            <div>
              {selectedSemesterId === 'ALL' ? (
                <div>
                  <div style={{ padding: '8px 12px', background: 'var(--paper)', border: '1px solid var(--line)', borderRadius: 6, marginBottom: 16, fontSize: 'var(--fs-xs)', color: 'var(--ink-soft)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <span>Showing master institution schedule for <strong>all {board.semesters.length} semesters</strong> stacked sequentially.</span>
                    <span className="badge badge-primary">{generationResult.classes.length} total sessions</span>
                  </div>
                  {board.semesters.map(sem => renderSemesterGrid(sem))}
                </div>
              ) : (
                (() => {
                  const targetSem = board.semesters.find(s => s.id === selectedSemesterId) || board.semesters[0]
                  return targetSem ? renderSemesterGrid(targetSem) : null
                })()
              )}
            </div>
          )}

          {/* SUB-VIEW 2: TEACHER SCHEDULE */}
          {matrixViewMode === 'teacher' && (
            <div className="card" style={{ padding: 'var(--sp-4)', background: 'var(--card-bg)', border: '1px solid var(--line)', borderRadius: 'var(--radius)' }}>
              {(() => {
                const teacher = board.teachers.find(t => t.id === selectedTeacherId)
                const teacherClasses = generationResult.classes.filter(c => c.teacher_id === selectedTeacherId)
                const freeDays = generationResult.teacher_free_days[selectedTeacherId] || []
                const prefInfo = generationResult.preferences[selectedTeacherId]

                return (
                  <div>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderBottom: '1px solid var(--line)', paddingBottom: 12, marginBottom: 16 }}>
                      <div>
                        <h3 style={{ fontFamily: 'var(--font-display)', fontSize: 'var(--fs-md)', fontWeight: 600, color: 'var(--ink)', margin: 0 }}>
                          {teacher?.name || selectedTeacherId} ({teacher?.internal ? 'Internal Faculty' : 'External Visiting'})
                        </h3>
                        <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--ink-soft)', marginTop: 4 }}>
                          Teaching load: {teacherClasses.length} sessions/week
                        </div>
                      </div>
                      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                        <span style={{ background: 'rgba(16, 185, 129, 0.1)', color: '#065f46', padding: '4px 8px', borderRadius: 6, fontWeight: 600, fontSize: 'var(--fs-xs)' }}>
                          🎉 Free Day(s): {freeDays.length > 0 ? freeDays.join(', ') : 'None'}
                        </span>
                        {prefInfo && (
                          <span style={{ background: 'rgba(0, 139, 135, 0.1)', color: 'var(--accent)', padding: '4px 8px', borderRadius: 6, fontWeight: 600, fontSize: 'var(--fs-xs)' }}>
                            Honoured: {prefInfo.honoured_ranks?.length ?? 0}/{prefInfo.achievable ?? 0}
                          </span>
                        )}
                      </div>
                    </div>

                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 10 }}>
                      {teacherClasses.map((c, i) => (
                        <div key={i} style={{ padding: 12, background: 'var(--paper)', border: '1px solid var(--line)', borderRadius: 6, fontSize: 'var(--fs-xs)' }}>
                          <div style={{ display: 'flex', justifyContent: 'space-between', fontWeight: 600 }}>
                            <span style={{ color: 'var(--accent)' }}>{c.day} · {c.slot_title}</span>
                            <span>{c.start}–{c.end}</span>
                          </div>
                          <div style={{ fontSize: 'var(--fs-sm)', fontWeight: 700, color: 'var(--ink)', marginTop: 4 }}>
                            {c.subject_code} — {c.subject_name}
                          </div>
                          <div style={{ display: 'flex', justifyContent: 'space-between', color: 'var(--ink-soft)', marginTop: 6 }}>
                            <span>Sem: {c.semester_id}</span>
                            <span style={{ fontWeight: 600, color: 'var(--ink)' }}>Room {c.room}</span>
                          </div>
                          {c.preference_rank && (
                            <div style={{ marginTop: 6, color: '#10b981', fontWeight: 600 }}>
                              ✓ Satisfies Preference #{c.preference_rank}
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                )
              })()}
            </div>
          )}

          {/* SUB-VIEW 3: PREFERENCES AUDIT */}
          {matrixViewMode === 'preferences' && (
            <div className="card" style={{ padding: 'var(--sp-4)', background: 'var(--card-bg)', border: '1px solid var(--line)', borderRadius: 'var(--radius)' }}>
              <h3 style={{ fontFamily: 'var(--font-display)', fontSize: 'var(--fs-md)', fontWeight: 600, color: 'var(--ink)', marginBottom: 12 }}>
                Teacher Free Days & Preferences Audit Report
              </h3>
              <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left', fontSize: 'var(--fs-xs)' }}>
                <thead>
                  <tr style={{ background: 'var(--paper)', borderBottom: '2px solid var(--line)' }}>
                    <th style={{ padding: '8px 12px' }}>Faculty Member</th>
                    <th style={{ padding: '8px 12px' }}>Type</th>
                    <th style={{ padding: '8px 12px' }}>Guaranteed Free Day(s)</th>
                    <th style={{ padding: '8px 12px' }}>Requested Preferences</th>
                    <th style={{ padding: '8px 12px' }}>Honoured Ranks</th>
                    <th style={{ padding: '8px 12px' }}>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {board.teachers.map((t, idx) => {
                    const free = generationResult.teacher_free_days[t.id] || []
                    const pref = generationResult.preferences[t.id]
                    const ranks = pref?.honoured_ranks || []
                    return (
                      <tr key={idx} style={{ borderBottom: '1px solid var(--line)' }}>
                        <td style={{ padding: '8px 12px', fontWeight: 600, color: 'var(--ink)' }}>
                          {t.name} <span style={{ color: 'var(--ink-soft)', fontFamily: 'var(--font-mono)' }}>({t.id})</span>
                        </td>
                        <td style={{ padding: '8px 12px' }}>
                          <span className={`badge ${t.internal ? 'badge-green' : 'badge-yellow'}`} style={{ fontSize: 9 }}>
                            {t.internal ? 'Internal' : 'External'}
                          </span>
                        </td>
                        <td style={{ padding: '8px 12px', fontWeight: 600, color: free.length ? '#065f46' : 'var(--ink-soft)' }}>
                          {free.join(', ') || 'None'}
                        </td>
                        <td style={{ padding: '8px 12px' }}>{pref?.preferences ?? 0}</td>
                        <td style={{ padding: '8px 12px' }}>
                          {ranks.length > 0 ? (
                            <span style={{ color: '#10b981', fontWeight: 600 }}>
                              {ranks.map((r: number) => `#${r}`).join(', ')}
                            </span>
                          ) : '—'}
                        </td>
                        <td style={{ padding: '8px 12px' }}>
                          <span style={{
                            background: ranks.length >= (pref?.achievable ?? 1) ? 'rgba(16, 185, 129, 0.1)' : 'rgba(245, 158, 11, 0.1)',
                            color: ranks.length >= (pref?.achievable ?? 1) ? '#065f46' : '#b45309',
                            padding: '2px 6px', borderRadius: 4, fontWeight: 600
                          }}>
                            {ranks.length}/{pref?.achievable ?? 0}
                          </span>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}

          {/* SUB-VIEW 4: ALL CLASSES TABLE */}
          {matrixViewMode === 'all' && (
            <div className="card" style={{ padding: 'var(--sp-4)', background: 'var(--card-bg)', border: '1px solid var(--line)', borderRadius: 'var(--radius)' }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
                <h3 style={{ fontFamily: 'var(--font-display)', fontSize: 'var(--fs-md)', fontWeight: 600, color: 'var(--ink)', margin: 0 }}>
                  All Scheduled Sessions ({generationResult.classes.length})
                </h3>
                <input
                  type="text"
                  placeholder="Filter by subject, teacher, room…"
                  className="form-control"
                  style={{ width: 240 }}
                  value={searchTerm}
                  onChange={e => setSearchTerm(e.target.value)}
                />
              </div>

              <div style={{ maxHeight: 420, overflowY: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left', fontSize: 'var(--fs-xs)' }}>
                  <thead>
                    <tr style={{ background: 'var(--paper)', borderBottom: '2px solid var(--line)' }}>
                      <th style={{ padding: '8px 10px' }}>Day</th>
                      <th style={{ padding: '8px 10px' }}>Slot</th>
                      <th style={{ padding: '8px 10px' }}>Time</th>
                      <th style={{ padding: '8px 10px' }}>Semester</th>
                      <th style={{ padding: '8px 10px' }}>Code</th>
                      <th style={{ padding: '8px 10px' }}>Subject Name</th>
                      <th style={{ padding: '8px 10px' }}>Faculty</th>
                      <th style={{ padding: '8px 10px' }}>Type</th>
                      <th style={{ padding: '8px 10px' }}>Room</th>
                      <th style={{ padding: '8px 10px' }}>Pref</th>
                    </tr>
                  </thead>
                  <tbody>
                    {generationResult.classes
                      .filter(c =>
                        !searchTerm ||
                        c.subject_code.toLowerCase().includes(searchTerm.toLowerCase()) ||
                        c.subject_name.toLowerCase().includes(searchTerm.toLowerCase()) ||
                        c.teacher_name.toLowerCase().includes(searchTerm.toLowerCase()) ||
                        c.room.toLowerCase().includes(searchTerm.toLowerCase()) ||
                        c.semester_id.toLowerCase().includes(searchTerm.toLowerCase())
                      )
                      .map((c, i) => (
                        <tr key={i} style={{ borderBottom: '1px solid var(--line)' }}>
                          <td style={{ padding: '8px 10px', fontWeight: 600 }}>{c.day}</td>
                          <td style={{ padding: '8px 10px' }}>{c.slot_title}</td>
                          <td style={{ padding: '8px 10px', color: 'var(--ink-soft)' }}>{c.start}–{c.end}</td>
                          <td style={{ padding: '8px 10px' }}>{c.semester_id}</td>
                          <td style={{ padding: '8px 10px', fontWeight: 700, color: 'var(--ink)' }}>{c.subject_code}</td>
                          <td style={{ padding: '8px 10px' }}>{c.subject_name}</td>
                          <td style={{ padding: '8px 10px' }}>{c.teacher_name}</td>
                          <td style={{ padding: '8px 10px' }}>
                            <span className={`badge ${c.class_type.toLowerCase() === 'practical' ? 'badge-yellow' : ''}`}>
                              {c.class_type}
                            </span>
                          </td>
                          <td style={{ padding: '8px 10px', fontWeight: 600 }}>{c.room}</td>
                          <td style={{ padding: '8px 10px' }}>
                            {c.preference_rank ? <span style={{ color: '#10b981', fontWeight: 600 }}>#{c.preference_rank}</span> : '—'}
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

      {/* ─────────────────────────────────────────────────────────── */}
      {/* MODAL 1: FILE INGESTION & EXTRACTION                      */}
      {/* ─────────────────────────────────────────────────────────── */}
      {showImportModal && (
        <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 9999, padding: 16 }}>
          <div style={{ background: 'var(--card-bg)', border: '1px solid var(--line)', borderRadius: 'var(--radius-lg)', boxShadow: 'var(--shadow)', width: '100%', maxWidth: 760, maxHeight: '90vh', overflowY: 'auto', display: 'flex', flexDirection: 'column' }}>
            {/* Modal Header */}
            <div style={{ padding: '16px 20px', borderBottom: '1px solid var(--line)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <div>
                <h2 style={{ fontFamily: 'var(--font-display)', fontSize: 'var(--fs-lg)', fontWeight: 700, color: 'var(--ink)', margin: 0, display: 'flex', alignItems: 'center', gap: 8 }}>
                  <Upload size={18} style={{ color: 'var(--accent)' }} />
                  Import Curriculum Files
                </h2>
                <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--ink-soft)', marginTop: 2 }}>
                  Extract semesters, courses, and teacher preferences from files or paste CSV/TSV text.
                </div>
              </div>
              <button className="btn-icon" onClick={() => setShowImportModal(false)}>
                <X size={16} />
              </button>
            </div>

            {/* Modal Subtabs */}
            <div style={{ display: 'flex', borderBottom: '1px solid var(--line)', background: 'var(--paper)' }}>
              <button
                className="btn btn-ghost btn-sm"
                style={{ flex: 1, borderRadius: 0, borderBottom: activeImportTab === 'subjects' ? '2px solid var(--accent)' : '2px solid transparent', fontWeight: activeImportTab === 'subjects' ? 600 : 400 }}
                onClick={() => setActiveImportTab('subjects')}
              >
                1. Subject Allocations File {subjectsFileContent && '✓'}
              </button>
              <button
                className="btn btn-ghost btn-sm"
                style={{ flex: 1, borderRadius: 0, borderBottom: activeImportTab === 'teachers' ? '2px solid var(--accent)' : '2px solid transparent', fontWeight: activeImportTab === 'teachers' ? 600 : 400 }}
                onClick={() => setActiveImportTab('teachers')}
              >
                2. Teacher Preferences File {teachersFileContent && '✓'}
              </button>
              {extractedData && (
                <button
                  className="btn btn-ghost btn-sm"
                  style={{ flex: 1, borderRadius: 0, borderBottom: activeImportTab === 'preview' ? '2px solid var(--accent)' : '2px solid transparent', fontWeight: activeImportTab === 'preview' ? 600 : 400, color: 'var(--accent)' }}
                  onClick={() => setActiveImportTab('preview')}
                >
                  3. Extraction Preview
                </button>
              )}
            </div>

            {/* Modal Body */}
            <div style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 16 }}>
              {activeImportTab === 'subjects' && (
                <div>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
                    <div style={{ fontWeight: 600, fontSize: 'var(--fs-sm)', color: 'var(--ink)' }}>
                      Subject Codes & Teacher Allocations File
                    </div>
                    <button
                      className="btn btn-ghost btn-sm"
                      style={{ fontSize: 11, gap: 4 }}
                      onClick={() => downloadCSVFile('subjects_allocation_template.csv', DEFAULT_SUBJECTS_TEMPLATE)}
                    >
                      <Download size={12} /> Download Sample Template
                    </button>
                  </div>
                  <p style={{ fontSize: 'var(--fs-xs)', color: 'var(--ink-soft)', margin: '0 0 10px' }}>
                    Covers current semesters of <strong>B.Tech, M.Tech, M.Sc</strong> with subject code, subject name, internal & external teacher ID, class type (Theory/Practical), sessions, and required facility.
                  </p>

                  <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 12 }}>
                    <label className="btn btn-sm btn-ghost" style={{ border: '1px solid var(--line)', cursor: 'pointer', gap: 6 }}>
                      <Upload size={12} /> Choose CSV / TSV File…
                      <input
                        type="file"
                        accept=".csv,.tsv,.txt"
                        style={{ display: 'none' }}
                        onChange={e => {
                          const file = e.target.files?.[0]
                          if (file) handleReadFile(file, 'subjects')
                        }}
                      />
                    </label>
                    {subjectsFileName && (
                      <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--accent)', fontWeight: 600 }}>
                        {subjectsFileName}
                      </span>
                    )}
                  </div>

                  <textarea
                    className="form-control"
                    rows={8}
                    style={{ fontFamily: 'var(--font-mono)', fontSize: 11, width: '100%', resize: 'vertical' }}
                    placeholder={DEFAULT_SUBJECTS_TEMPLATE}
                    value={subjectsFileContent}
                    onChange={e => setSubjectsFileContent(e.target.value)}
                  />
                  <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 12 }}>
                    <button className="btn btn-sm btn-primary" onClick={() => setActiveImportTab('teachers')}>
                      Next: Teacher Preferences File →
                    </button>
                  </div>
                </div>
              )}

              {activeImportTab === 'teachers' && (
                <div>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
                    <div style={{ fontWeight: 600, fontSize: 'var(--fs-sm)', color: 'var(--ink)' }}>
                      Teacher Profiles & 3 Preferred Slots File
                    </div>
                    <button
                      className="btn btn-ghost btn-sm"
                      style={{ fontSize: 11, gap: 4 }}
                      onClick={() => downloadCSVFile('teacher_preferences_template.csv', DEFAULT_TEACHERS_TEMPLATE)}
                    >
                      <Download size={12} /> Download Sample Template
                    </button>
                  </div>
                  <p style={{ fontSize: 'var(--fs-xs)', color: 'var(--ink-soft)', margin: '0 0 10px' }}>
                    Contains Teacher ID, Name, and up to 3 preferred slots: Day, Time slot (<strong>10-12:00, 12-2, 2:30-4:30</strong>), and Facility preference (<strong>black board, projector, blackboard and projector</strong>).
                  </p>

                  <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 12 }}>
                    <label className="btn btn-sm btn-ghost" style={{ border: '1px solid var(--line)', cursor: 'pointer', gap: 6 }}>
                      <Upload size={12} /> Choose CSV / TSV File…
                      <input
                        type="file"
                        accept=".csv,.tsv,.txt"
                        style={{ display: 'none' }}
                        onChange={e => {
                          const file = e.target.files?.[0]
                          if (file) handleReadFile(file, 'teachers')
                        }}
                      />
                    </label>
                    {teachersFileName && (
                      <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--accent)', fontWeight: 600 }}>
                        {teachersFileName}
                      </span>
                    )}
                  </div>

                  <textarea
                    className="form-control"
                    rows={8}
                    style={{ fontFamily: 'var(--font-mono)', fontSize: 11, width: '100%', resize: 'vertical' }}
                    placeholder={DEFAULT_TEACHERS_TEMPLATE}
                    value={teachersFileContent}
                    onChange={e => setTeachersFileContent(e.target.value)}
                  />
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 12 }}>
                    <button className="btn btn-sm btn-ghost" onClick={() => setActiveImportTab('subjects')}>
                      ← Back to Subjects File
                    </button>
                    <button className="btn btn-sm btn-primary" onClick={handleExtractFiles} disabled={extracting} style={{ gap: 6 }}>
                      {extracting ? <RefreshCw size={12} className="spin" /> : <Play size={12} />}
                      {extracting ? 'Extracting…' : 'Extract Data & Preview'}
                    </button>
                  </div>
                </div>
              )}

              {activeImportTab === 'preview' && extractedData && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                  <div style={{ background: 'rgba(16, 185, 129, 0.08)', border: '1px solid #10b981', borderRadius: 'var(--radius)', padding: 14 }}>
                    <div style={{ fontWeight: 700, color: '#065f46', display: 'flex', alignItems: 'center', gap: 6 }}>
                      <CheckCircle2 size={16} /> Data Extraction Succeeded!
                    </div>
                    <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--ink)', marginTop: 8, display: 'flex', flexDirection: 'column', gap: 4 }}>
                      <div>• <strong>{extractedData.summary.semesters_count} Semesters</strong>: {extractedData.semesters.map(s => `${s.id} (${s.programme} S${s.number})`).join(', ')}</div>
                      <div>• <strong>{extractedData.summary.subjects_count} Subjects</strong>: {extractedData.subjects.slice(0, 8).map(s => s.code).join(', ')}{extractedData.subjects.length > 8 ? '…' : ''}</div>
                      <div>• <strong>{extractedData.summary.teachers_count} Teachers</strong> with time slot preferences</div>
                    </div>
                  </div>

                  <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
                    <button className="btn btn-ghost btn-sm" onClick={() => setActiveImportTab('teachers')}>
                      Edit Inputs
                    </button>
                    <button
                      className="btn btn-ghost btn-sm"
                      onClick={() => handleApplyExtractedData('merge')}
                      title="Merge into existing board"
                    >
                      Merge with Existing
                    </button>
                    <button
                      className="btn btn-primary btn-sm"
                      style={{ background: 'var(--accent)', color: '#fff' }}
                      onClick={() => handleApplyExtractedData('replace')}
                      title="Replace current board entities with extracted data"
                    >
                      Replace Board (Clean Slate)
                    </button>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* ─────────────────────────────────────────────────────────── */}
      {/* MODAL 2: ADD SEMESTER                                      */}
      {/* ─────────────────────────────────────────────────────────── */}
      {showAddSemesterModal && (
        <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 9999, padding: 16 }}>
          <div style={{ background: 'var(--card-bg)', border: '1px solid var(--line)', borderRadius: 'var(--radius-lg)', boxShadow: 'var(--shadow)', width: '100%', maxWidth: 480, padding: 20 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
              <h3 style={{ fontFamily: 'var(--font-display)', fontSize: 'var(--fs-md)', fontWeight: 700, margin: 0 }}>Add Academic Semester</h3>
              <button className="btn-icon" onClick={() => setShowAddSemesterModal(false)}><X size={16} /></button>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              <div>
                <label className="mono-label">Programme</label>
                <select
                  className="form-control"
                  value={newSemester.programme}
                  onChange={e => {
                    const prog = e.target.value
                    const prefix = prog === 'B.Tech' ? 'BT' : prog === 'M.Tech' ? 'MT' : prog === 'M.Sc' ? 'MS' : 'SEM'
                    setNewSemester({
                      ...newSemester,
                      programme: prog,
                      id: `${prefix}-${newSemester.number}`,
                      name: `${prog} Semester ${newSemester.number}`,
                    })
                  }}
                >
                  <option value="B.Tech">B.Tech</option>
                  <option value="M.Tech">M.Tech</option>
                  <option value="M.Sc">M.Sc</option>
                  <option value="MCA">MCA</option>
                  <option value="MBA">MBA</option>
                </select>
              </div>

              <div>
                <label className="mono-label">Semester Number</label>
                <input
                  type="number"
                  min="1"
                  max="10"
                  className="form-control"
                  value={newSemester.number}
                  onChange={e => {
                    const num = parseInt(e.target.value, 10) || 1
                    const prefix = newSemester.programme === 'B.Tech' ? 'BT' : newSemester.programme === 'M.Tech' ? 'MT' : newSemester.programme === 'M.Sc' ? 'MS' : 'SEM'
                    setNewSemester({
                      ...newSemester,
                      number: num,
                      id: `${prefix}-${num}`,
                      name: `${newSemester.programme} Semester ${num}`,
                    })
                  }}
                />
              </div>

              <div>
                <label className="mono-label">Unique Semester ID</label>
                <input
                  type="text"
                  className="form-control"
                  value={newSemester.id}
                  onChange={e => setNewSemester({ ...newSemester, id: e.target.value })}
                />
              </div>

              <div>
                <label className="mono-label">Display Name</label>
                <input
                  type="text"
                  className="form-control"
                  value={newSemester.name}
                  onChange={e => setNewSemester({ ...newSemester, name: e.target.value })}
                />
              </div>

              <div>
                <label className="mono-label">Student Cohort Strength</label>
                <input
                  type="number"
                  min="1"
                  className="form-control"
                  value={newSemester.strength}
                  onChange={e => setNewSemester({ ...newSemester, strength: parseInt(e.target.value, 10) || 60 })}
                />
              </div>

              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 12 }}>
                <button className="btn btn-ghost btn-sm" onClick={() => setShowAddSemesterModal(false)}>Cancel</button>
                <button
                  className="btn btn-primary btn-sm"
                  onClick={() => {
                    if (!newSemester.id.trim()) return
                    setBoard({
                      ...board,
                      semesters: [...board.semesters, newSemester],
                    })
                    setShowAddSemesterModal(false)
                  }}
                >
                  Add Semester
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ─────────────────────────────────────────────────────────── */}
      {/* MODAL 3: ADD ROOM                                          */}
      {/* ─────────────────────────────────────────────────────────── */}
      {showAddRoomModal && (
        <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 9999, padding: 16 }}>
          <div style={{ background: 'var(--card-bg)', border: '1px solid var(--line)', borderRadius: 'var(--radius-lg)', boxShadow: 'var(--shadow)', width: '100%', maxWidth: 440, padding: 20 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
              <h3 style={{ fontFamily: 'var(--font-display)', fontSize: 'var(--fs-md)', fontWeight: 700, margin: 0 }}>Add Room / Laboratory</h3>
              <button className="btn-icon" onClick={() => setShowAddRoomModal(false)}><X size={16} /></button>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              <div>
                <label className="mono-label">Room Number / Identifier</label>
                <input
                  type="text"
                  className="form-control"
                  placeholder="e.g. CR-101, Lab-2"
                  value={newRoom.number}
                  onChange={e => setNewRoom({ ...newRoom, number: e.target.value })}
                />
              </div>

              <div>
                <label className="mono-label">Room Kind</label>
                <select
                  className="form-control"
                  value={newRoom.kind}
                  onChange={e => setNewRoom({ ...newRoom, kind: e.target.value as 'classroom' | 'lab' })}
                >
                  <option value="classroom">Classroom (Lecture Hall)</option>
                  <option value="lab">Laboratory (Practical Block)</option>
                </select>
              </div>

              <div>
                <label className="mono-label">Facility Provided</label>
                <select
                  className="form-control"
                  value={newRoom.facility}
                  onChange={e => setNewRoom({ ...newRoom, facility: e.target.value })}
                >
                  <option value="blackboard">Blackboard</option>
                  <option value="projector">Projector</option>
                  <option value="both">Both (Blackboard & Projector)</option>
                  <option value="none">Standard / None</option>
                </select>
              </div>

              <div>
                <label className="mono-label">Capacity (Students)</label>
                <input
                  type="number"
                  min="1"
                  className="form-control"
                  value={newRoom.capacity}
                  onChange={e => setNewRoom({ ...newRoom, capacity: parseInt(e.target.value, 10) || 60 })}
                />
              </div>

              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 12 }}>
                <button className="btn btn-ghost btn-sm" onClick={() => setShowAddRoomModal(false)}>Cancel</button>
                <button
                  className="btn btn-primary btn-sm"
                  onClick={() => {
                    if (!newRoom.number.trim()) return
                    setBoard({
                      ...board,
                      rooms: [...board.rooms, newRoom],
                    })
                    setShowAddRoomModal(false)
                  }}
                >
                  Add Room
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ─────────────────────────────────────────────────────────── */}
      {/* MODAL 4: ADD TEACHER & 3 PREFERENCES                       */}
      {/* ─────────────────────────────────────────────────────────── */}
      {showAddTeacherModal && (
        <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 9999, padding: 16 }}>
          <div style={{ background: 'var(--card-bg)', border: '1px solid var(--line)', borderRadius: 'var(--radius-lg)', boxShadow: 'var(--shadow)', width: '100%', maxWidth: 600, maxHeight: '90vh', overflowY: 'auto', padding: 20 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
              <h3 style={{ fontFamily: 'var(--font-display)', fontSize: 'var(--fs-md)', fontWeight: 700, margin: 0 }}>Add Faculty Member & Preferences</h3>
              <button className="btn-icon" onClick={() => setShowAddTeacherModal(false)}><X size={16} /></button>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 2fr', gap: 10 }}>
                <div>
                  <label className="mono-label">Teacher ID</label>
                  <input
                    type="text"
                    className="form-control"
                    placeholder="e.g. I05, EXT-01"
                    value={newTeacher.id}
                    onChange={e => setNewTeacher({ ...newTeacher, id: e.target.value })}
                  />
                </div>
                <div>
                  <label className="mono-label">Full Name</label>
                  <input
                    type="text"
                    className="form-control"
                    placeholder="e.g. Dr. Rajesh Sharma"
                    value={newTeacher.name}
                    onChange={e => setNewTeacher({ ...newTeacher, name: e.target.value })}
                  />
                </div>
              </div>

              <div>
                <label className="mono-label">Faculty Type</label>
                <select
                  className="form-control"
                  value={newTeacher.internal ? 'internal' : 'external'}
                  onChange={e => setNewTeacher({ ...newTeacher, internal: e.target.value === 'internal' })}
                >
                  <option value="internal">Internal Faculty (Guaranteed Free Day Target)</option>
                  <option value="external">External Visiting Faculty</option>
                </select>
              </div>

              {/* Preferences Header */}
              <div style={{ borderTop: '1px solid var(--line)', paddingTop: 10 }}>
                <div style={{ fontWeight: 600, fontSize: 'var(--fs-sm)', color: 'var(--ink)' }}>
                  3 Preferred Time Slots
                </div>
                <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--ink-soft)', marginBottom: 10 }}>
                  Time slots: 10-12:00 (Slot 1), 12-2 (Slot 2), 2:30-4:30 (Slot 3). Facilities: Blackboard, Projector, Both.
                </div>

                {/* Preference 1 */}
                <div style={{ background: 'var(--paper)', padding: 10, borderRadius: 6, marginBottom: 8 }}>
                  <div className="mono-label" style={{ color: 'var(--accent)', marginBottom: 4 }}>Preference #1</div>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.5fr 1.5fr', gap: 8 }}>
                    <select className="form-control" value={newTeacher.pref1_day} onChange={e => setNewTeacher({ ...newTeacher, pref1_day: e.target.value })}>
                      {board.config.working_days.map(d => <option key={d} value={d}>{d}</option>)}
                    </select>
                    <select className="form-control" value={newTeacher.pref1_slot} onChange={e => setNewTeacher({ ...newTeacher, pref1_slot: parseInt(e.target.value, 10) })}>
                      <option value="1">10-12:00 (Slot 1)</option>
                      <option value="2">12-2 (Slot 2)</option>
                      <option value="3">2:30-4:30 (Slot 3)</option>
                    </select>
                    <select className="form-control" value={newTeacher.pref1_facility} onChange={e => setNewTeacher({ ...newTeacher, pref1_facility: e.target.value })}>
                      <option value="blackboard and projector">Blackboard & Projector</option>
                      <option value="black board">Black Board</option>
                      <option value="projector">Projector</option>
                    </select>
                  </div>
                </div>

                {/* Preference 2 */}
                <div style={{ background: 'var(--paper)', padding: 10, borderRadius: 6, marginBottom: 8 }}>
                  <div className="mono-label" style={{ color: 'var(--accent)', marginBottom: 4 }}>Preference #2</div>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.5fr 1.5fr', gap: 8 }}>
                    <select className="form-control" value={newTeacher.pref2_day} onChange={e => setNewTeacher({ ...newTeacher, pref2_day: e.target.value })}>
                      {board.config.working_days.map(d => <option key={d} value={d}>{d}</option>)}
                    </select>
                    <select className="form-control" value={newTeacher.pref2_slot} onChange={e => setNewTeacher({ ...newTeacher, pref2_slot: parseInt(e.target.value, 10) })}>
                      <option value="1">10-12:00 (Slot 1)</option>
                      <option value="2">12-2 (Slot 2)</option>
                      <option value="3">2:30-4:30 (Slot 3)</option>
                    </select>
                    <select className="form-control" value={newTeacher.pref2_facility} onChange={e => setNewTeacher({ ...newTeacher, pref2_facility: e.target.value })}>
                      <option value="blackboard and projector">Blackboard & Projector</option>
                      <option value="black board">Black Board</option>
                      <option value="projector">Projector</option>
                    </select>
                  </div>
                </div>

                {/* Preference 3 */}
                <div style={{ background: 'var(--paper)', padding: 10, borderRadius: 6, marginBottom: 8 }}>
                  <div className="mono-label" style={{ color: 'var(--accent)', marginBottom: 4 }}>Preference #3</div>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.5fr 1.5fr', gap: 8 }}>
                    <select className="form-control" value={newTeacher.pref3_day} onChange={e => setNewTeacher({ ...newTeacher, pref3_day: e.target.value })}>
                      {board.config.working_days.map(d => <option key={d} value={d}>{d}</option>)}
                    </select>
                    <select className="form-control" value={newTeacher.pref3_slot} onChange={e => setNewTeacher({ ...newTeacher, pref3_slot: parseInt(e.target.value, 10) })}>
                      <option value="1">10-12:00 (Slot 1)</option>
                      <option value="2">12-2 (Slot 2)</option>
                      <option value="3">2:30-4:30 (Slot 3)</option>
                    </select>
                    <select className="form-control" value={newTeacher.pref3_facility} onChange={e => setNewTeacher({ ...newTeacher, pref3_facility: e.target.value })}>
                      <option value="blackboard and projector">Blackboard & Projector</option>
                      <option value="black board">Black Board</option>
                      <option value="projector">Projector</option>
                    </select>
                  </div>
                </div>
              </div>

              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 12 }}>
                <button className="btn btn-ghost btn-sm" onClick={() => setShowAddTeacherModal(false)}>Cancel</button>
                <button
                  className="btn btn-primary btn-sm"
                  onClick={async () => {
                    if (!newTeacher.id.trim() || !newTeacher.name.trim()) return
                    const derivedShort = deriveTeacherShortForm(newTeacher.name.trim(), newTeacher.id.trim())
                    const compiledTeacher: GeneratorTeacher = {
                      id: derivedShort,
                      name: newTeacher.name.trim(),
                      internal: newTeacher.internal,
                      short_name: derivedShort,
                      preferences: [
                        { day: newTeacher.pref1_day, slot: newTeacher.pref1_slot, facility: newTeacher.pref1_facility },
                        { day: newTeacher.pref2_day, slot: newTeacher.pref2_slot, facility: newTeacher.pref2_facility },
                        { day: newTeacher.pref3_day, slot: newTeacher.pref3_slot, facility: newTeacher.pref3_facility },
                      ],
                      unavailable: [],
                      unavailable_days: newTeacher.unavailable_days,
                    }

                    try {
                      const res = await createGeneratorFaculty({
                        name: newTeacher.name.trim(),
                        id: derivedShort,
                        is_internal: newTeacher.internal,
                      })
                      compiledTeacher.auto_email = res.email
                      compiledTeacher.auto_password = res.password
                      setLatestSingleAccount({
                        name: res.full_name,
                        short: res.short_name,
                        email: res.email,
                        password: res.password,
                      })
                      useWorkspaceStore.getState().loadCatalog()
                    } catch (e: any) {
                      console.warn('Could not sync faculty to backend catalog:', e.message)
                    }

                    setBoard({
                      ...board,
                      teachers: [...board.teachers, compiledTeacher],
                    })
                    setShowAddTeacherModal(false)
                  }}
                >
                  Add Faculty Member
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ─────────────────────────────────────────────────────────── */}
      {/* MODAL 5: ADD SUBJECT ALLOCATION                            */}
      {/* ─────────────────────────────────────────────────────────── */}
      {showAddSubjectModal && (
        <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 9999, padding: 16 }}>
          <div style={{ background: 'var(--card-bg)', border: '1px solid var(--line)', borderRadius: 'var(--radius-lg)', boxShadow: 'var(--shadow)', width: '100%', maxWidth: 520, padding: 20 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
              <h3 style={{ fontFamily: 'var(--font-display)', fontSize: 'var(--fs-md)', fontWeight: 700, margin: 0 }}>Add Course Subject & Allocation</h3>
              <button className="btn-icon" onClick={() => setShowAddSubjectModal(false)}><X size={16} /></button>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 2fr', gap: 10 }}>
                <div>
                  <label className="mono-label">Subject Code</label>
                  <input
                    type="text"
                    className="form-control"
                    placeholder="e.g. CS301"
                    value={newSubject.code}
                    onChange={e => setNewSubject({ ...newSubject, code: e.target.value })}
                  />
                </div>
                <div>
                  <label className="mono-label">Subject Title</label>
                  <input
                    type="text"
                    className="form-control"
                    placeholder="e.g. Data Structures & Algorithms"
                    value={newSubject.name}
                    onChange={e => setNewSubject({ ...newSubject, name: e.target.value })}
                  />
                </div>
              </div>

              <div>
                <label className="mono-label">Semester</label>
                <select
                  className="form-control"
                  value={newSubject.semester_id}
                  onChange={e => setNewSubject({ ...newSubject, semester_id: e.target.value })}
                >
                  <option value="">Select Semester…</option>
                  {board.semesters.map(s => (
                    <option key={s.id} value={s.id}>{s.id} ({s.programme} S{s.number})</option>
                  ))}
                </select>
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                <div>
                  <label className="mono-label">Internal / Primary Faculty</label>
                  <select
                    className="form-control"
                    value={newSubject.internal_teacher_id}
                    onChange={e => setNewSubject({ ...newSubject, internal_teacher_id: e.target.value })}
                  >
                    <option value="">Select Faculty…</option>
                    {board.teachers.map(t => (
                      <option key={t.id} value={t.id}>{t.name} ({t.id})</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="mono-label">External Faculty (Optional)</label>
                  <select
                    className="form-control"
                    value={newSubject.external_teacher_id}
                    onChange={e => setNewSubject({ ...newSubject, external_teacher_id: e.target.value })}
                  >
                    <option value="">None / N/A</option>
                    {board.teachers.map(t => (
                      <option key={t.id} value={t.id}>{t.name} ({t.id})</option>
                    ))}
                  </select>
                </div>
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                <div>
                  <label className="mono-label">Class Type</label>
                  <select
                    className="form-control"
                    value={newSubject.class_type}
                    onChange={e => {
                      const ct = e.target.value as 'theory' | 'practical'
                      setNewSubject({
                        ...newSubject,
                        class_type: ct,
                        sessions_per_week: ct === 'practical' ? 1 : 3,
                      })
                    }}
                  >
                    <option value="theory">Theory (Lecture)</option>
                    <option value="practical">Practical (Laboratory)</option>
                  </select>
                </div>
                <div>
                  <label className="mono-label">Sessions Per Week (e.g. 2, 2.5, 3)</label>
                  <input
                    type="number"
                    step="0.5"
                    min="0.5"
                    max="10"
                    className="form-control"
                    value={newSubject.sessions_per_week}
                    onChange={e => setNewSubject({ ...newSubject, sessions_per_week: parseFloat(e.target.value) || 0.5 })}
                  />
                  <div style={{ fontSize: 10, color: 'var(--ink-soft)', marginTop: 2 }}>
                    Multiples of 0.5: 2 = 4h, 2.5 = 5h (two 2h + one 1h), 3 = 6h.
                  </div>
                </div>
              </div>

              <div>
                <label className="mono-label">Facility Requirement</label>
                <select
                  className="form-control"
                  value={newSubject.facility}
                  onChange={e => setNewSubject({ ...newSubject, facility: e.target.value })}
                >
                  <option value="none">Standard / None</option>
                  <option value="blackboard">Blackboard</option>
                  <option value="projector">Projector</option>
                  <option value="both">Both (Blackboard & Projector)</option>
                </select>
              </div>

              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 12 }}>
                <button className="btn btn-ghost btn-sm" onClick={() => setShowAddSubjectModal(false)}>Cancel</button>
                <button
                  className="btn btn-primary btn-sm"
                  onClick={async () => {
                    if (!newSubject.code.trim() || !newSubject.semester_id) {
                      setErrorMsg('Please specify Subject Code and select a Semester.')
                      return
                    }

                    const selectedSem = board.semesters.find(s => s.id === newSubject.semester_id)
                    const programName = selectedSem?.programme || 'Default Program'
                    const semNum = selectedSem?.number || 1
                    const teacherObj = board.teachers.find(t => t.id === newSubject.internal_teacher_id)
                    const teacherShort = teacherObj ? (teacherObj.short_name || deriveTeacherShortForm(teacherObj.name, teacherObj.id)) : undefined

                    try {
                      await createGeneratorSubject({
                        code: newSubject.code.trim(),
                        name: newSubject.name.trim() || newSubject.code.trim(),
                        program: programName,
                        semester: semNum,
                        class_type: newSubject.class_type,
                        weekly_hours: Math.round((newSubject.sessions_per_week || 3) * (newSubject.class_type === 'practical' ? 3 : 1)),
                        teacher_short_name: teacherShort,
                      })
                      useWorkspaceStore.getState().loadCatalog()
                    } catch (e: any) {
                      console.warn('Could not sync subject to catalog database:', e.message)
                    }

                    const updated = {
                      ...board,
                      subjects: [...board.subjects, newSubject],
                    }
                    setBoard(updated)
                    autoValidateBoard(updated)
                    setShowAddSubjectModal(false)
                  }}
                >
                  Add Subject
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ─────────────────────────────────────────────────────────── */}
      {/* MODAL 6: EDIT FACULTY MEMBER & PREFERENCES                 */}
      {/* ─────────────────────────────────────────────────────────── */}
      {editingTeacher && editingTeacherIdx !== null && (
        <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 9999, padding: 16 }}>
          <div style={{ background: 'var(--card-bg)', border: '1px solid var(--line)', borderRadius: 'var(--radius-lg)', boxShadow: 'var(--shadow)', width: '100%', maxWidth: 640, maxHeight: '90vh', overflowY: 'auto', padding: 20 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
              <div>
                <h3 style={{ fontFamily: 'var(--font-display)', fontSize: 'var(--fs-md)', fontWeight: 700, margin: 0, display: 'flex', alignItems: 'center', gap: 8 }}>
                  <Pencil size={16} style={{ color: 'var(--accent)' }} />
                  Edit Faculty: {editingTeacher.name} ({editingTeacher.id})
                </h3>
                <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--ink-soft)', marginTop: 2 }}>
                  Modify name, internal/external status, preferred time slots, or unavailabilities.
                </div>
              </div>
              <button className="btn-icon" onClick={() => { setEditingTeacher(null); setEditingTeacherIdx(null); }}><X size={16} /></button>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 2fr', gap: 10 }}>
                <div>
                  <label className="mono-label">Teacher ID</label>
                  <input
                    type="text"
                    className="form-control"
                    value={editingTeacher.id}
                    onChange={e => setEditingTeacher({ ...editingTeacher, id: e.target.value })}
                  />
                </div>
                <div>
                  <label className="mono-label">Full Name</label>
                  <input
                    type="text"
                    className="form-control"
                    value={editingTeacher.name}
                    onChange={e => setEditingTeacher({ ...editingTeacher, name: e.target.value })}
                  />
                </div>
              </div>

              <div>
                <label className="mono-label">Faculty Type & Workload Policy</label>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                  <button
                    type="button"
                    className={`btn btn-sm ${editingTeacher.internal ? 'btn-primary' : 'btn-ghost'}`}
                    style={{ justifyContent: 'center', gap: 6 }}
                    onClick={() => setEditingTeacher({ ...editingTeacher, internal: true })}
                  >
                    Internal Faculty (Free Day Target)
                  </button>
                  <button
                    type="button"
                    className={`btn btn-sm ${!editingTeacher.internal ? 'btn-primary' : 'btn-ghost'}`}
                    style={{ justifyContent: 'center', gap: 6 }}
                    onClick={() => setEditingTeacher({ ...editingTeacher, internal: false })}
                  >
                    External Visiting Faculty
                  </button>
                </div>
                <div style={{ fontSize: 11, color: 'var(--ink-soft)', marginTop: 4 }}>
                  {editingTeacher.internal
                    ? '💡 Internal faculty are granted 1 guaranteed weekly free research day and are limited to at most 1 theory class per day.'
                    : '💡 External visiting faculty have no guaranteed free day requirement and can accommodate higher session loads across days.'}
                </div>
              </div>

              {/* Preferences Section */}
              <div style={{ borderTop: '1px solid var(--line)', paddingTop: 12 }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
                  <div>
                    <div style={{ fontWeight: 600, fontSize: 'var(--fs-sm)', color: 'var(--ink)' }}>
                      Preferred Time Slots ({editingTeacher.preferences.length}/3)
                    </div>
                    <div style={{ fontSize: 11, color: 'var(--ink-soft)' }}>
                      Slots: 10-12:00 (Slot 1), 12-2 (Slot 2), 2:30-4:30 (Slot 3)
                    </div>
                  </div>
                  {editingTeacher.preferences.length < 3 && (
                    <button
                      type="button"
                      className="btn btn-ghost btn-sm"
                      style={{ fontSize: 11, color: 'var(--accent)', gap: 4 }}
                      onClick={() => {
                        setEditingTeacher({
                          ...editingTeacher,
                          preferences: [
                            ...editingTeacher.preferences,
                            { day: board.config.working_days[0] || 'Mon', slot: 1, facility: 'blackboard and projector' }
                          ]
                        })
                      }}
                    >
                      <Plus size={12} /> Add Slot
                    </button>
                  )}
                </div>

                {editingTeacher.preferences.length === 0 ? (
                  <div style={{ padding: 12, textAlign: 'center', color: 'var(--ink-soft)', fontSize: 11, border: '1px dashed var(--line)', borderRadius: 6 }}>
                    No preferences configured. Click "+ Add Slot" to declare a preferred time.
                  </div>
                ) : (
                  editingTeacher.preferences.map((p, pIdx) => (
                    <div key={pIdx} style={{ background: 'var(--paper)', padding: 10, borderRadius: 6, marginBottom: 8 }}>
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
                        <span className="mono-label" style={{ color: 'var(--accent)' }}>Preference #{pIdx + 1}</span>
                        <button
                          type="button"
                          className="btn-icon"
                          style={{ color: 'var(--violation)', width: 20, height: 20 }}
                          onClick={() => {
                            const nextPrefs = editingTeacher.preferences.filter((_, i) => i !== pIdx)
                            setEditingTeacher({ ...editingTeacher, preferences: nextPrefs })
                          }}
                          title="Remove this slot"
                        >
                          <Trash2 size={11} />
                        </button>
                      </div>
                      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.4fr 1.6fr', gap: 8 }}>
                        <select
                          className="form-control"
                          value={p.day}
                          onChange={e => {
                            const nextPrefs = [...editingTeacher.preferences]
                            nextPrefs[pIdx].day = e.target.value
                            setEditingTeacher({ ...editingTeacher, preferences: nextPrefs })
                          }}
                        >
                          {board.config.working_days.map(d => <option key={d} value={d}>{d}</option>)}
                        </select>

                        <select
                          className="form-control"
                          value={p.slot}
                          onChange={e => {
                            const nextPrefs = [...editingTeacher.preferences]
                            nextPrefs[pIdx].slot = parseInt(e.target.value, 10)
                            setEditingTeacher({ ...editingTeacher, preferences: nextPrefs })
                          }}
                        >
                          <option value="1">10-12:00 (Slot 1)</option>
                          <option value="2">12-2 (Slot 2)</option>
                          <option value="3">2:30-4:30 (Slot 3)</option>
                          {board.config.allow_extension_slots && (
                            <>
                              <option value="0">08-10:00 (Early Ext)</option>
                              <option value="4">16:30-18:30 (Late Ext)</option>
                            </>
                          )}
                        </select>

                        <select
                          className="form-control"
                          value={p.facility}
                          onChange={e => {
                            const nextPrefs = [...editingTeacher.preferences]
                            nextPrefs[pIdx].facility = e.target.value
                            setEditingTeacher({ ...editingTeacher, preferences: nextPrefs })
                          }}
                        >
                          <option value="blackboard and projector">Blackboard & Projector</option>
                          <option value="black board">Black Board</option>
                          <option value="projector">Projector</option>
                          <option value="none">None / Standard</option>
                        </select>
                      </div>
                    </div>
                  ))
                )}
              </div>

              {/* Unavailable Days */}
              <div>
                <label className="mono-label">Unavailable Days (Blocked from Teaching)</label>
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 4 }}>
                  {board.config.working_days.map(day => {
                    const isUnavail = editingTeacher.unavailable_days.includes(day)
                    return (
                      <button
                        key={day}
                        type="button"
                        className={`badge ${isUnavail ? 'badge-yellow' : ''}`}
                        style={{ cursor: 'pointer', border: '1px solid var(--line)', background: isUnavail ? 'rgba(239,68,68,0.1)' : 'transparent', color: isUnavail ? 'var(--violation)' : 'var(--ink)' }}
                        onClick={() => {
                          const nextDays = isUnavail
                            ? editingTeacher.unavailable_days.filter(d => d !== day)
                            : [...editingTeacher.unavailable_days, day]
                          setEditingTeacher({ ...editingTeacher, unavailable_days: nextDays })
                        }}
                      >
                        {day} {isUnavail ? '✖' : ''}
                      </button>
                    )
                  })}
                </div>
              </div>

              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 12 }}>
                <button className="btn btn-ghost btn-sm" onClick={() => { setEditingTeacher(null); setEditingTeacherIdx(null); }}>Cancel</button>
                <button
                  className="btn btn-primary btn-sm"
                  onClick={() => {
                    if (!editingTeacher.id.trim() || !editingTeacher.name.trim()) return
                    const oldId = board.teachers[editingTeacherIdx].id
                    const newId = editingTeacher.id.trim()

                    const updatedTeachers = [...board.teachers]
                    updatedTeachers[editingTeacherIdx] = {
                      ...editingTeacher,
                      id: newId,
                      name: editingTeacher.name.trim(),
                    }

                    // Synchronize subject references if ID changed
                    let updatedSubjects = board.subjects
                    if (oldId !== newId) {
                      updatedSubjects = board.subjects.map(sub => {
                        let iId = sub.internal_teacher_id
                        let eId = sub.external_teacher_id
                        if (iId === oldId) iId = newId
                        if (eId === oldId) eId = newId
                        return { ...sub, internal_teacher_id: iId, external_teacher_id: eId }
                      })
                    }

                    const updatedBoard = {
                      ...board,
                      teachers: updatedTeachers,
                      subjects: updatedSubjects,
                    }
                    setBoard(updatedBoard)
                    autoValidateBoard(updatedBoard)
                    setEditingTeacher(null)
                    setEditingTeacherIdx(null)
                    setSaveSuccessMsg(`Updated faculty member ${editingTeacher.name} (${newId})`)
                  }}
                >
                  Save Changes
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ─────────────────────────────────────────────────────────── */}
      {/* MODAL 7: EDIT COURSE SUBJECT & ALLOCATION                  */}
      {/* ─────────────────────────────────────────────────────────── */}
      {editingSubject && editingSubjectIdx !== null && (
        <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 9999, padding: 16 }}>
          <div style={{ background: 'var(--card-bg)', border: '1px solid var(--line)', borderRadius: 'var(--radius-lg)', boxShadow: 'var(--shadow)', width: '100%', maxWidth: 540, maxHeight: '90vh', overflowY: 'auto', padding: 20 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
              <div>
                <h3 style={{ fontFamily: 'var(--font-display)', fontSize: 'var(--fs-md)', fontWeight: 700, margin: 0, display: 'flex', alignItems: 'center', gap: 8 }}>
                  <Pencil size={16} style={{ color: 'var(--accent)' }} />
                  Edit Subject: {editingSubject.code}
                </h3>
                <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--ink-soft)', marginTop: 2 }}>
                  Modify title, semester cohort, faculty assignments, session count, or facility requirement.
                </div>
              </div>
              <button className="btn-icon" onClick={() => { setEditingSubject(null); setEditingSubjectIdx(null); }}><X size={16} /></button>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 2fr', gap: 10 }}>
                <div>
                  <label className="mono-label">Subject Code</label>
                  <input
                    type="text"
                    className="form-control"
                    value={editingSubject.code}
                    onChange={e => setEditingSubject({ ...editingSubject, code: e.target.value })}
                  />
                </div>
                <div>
                  <label className="mono-label">Subject Title</label>
                  <input
                    type="text"
                    className="form-control"
                    value={editingSubject.name}
                    onChange={e => setEditingSubject({ ...editingSubject, name: e.target.value })}
                  />
                </div>
              </div>

              <div>
                <label className="mono-label">Assigned Semester</label>
                <select
                  className="form-control"
                  value={editingSubject.semester_id}
                  onChange={e => setEditingSubject({ ...editingSubject, semester_id: e.target.value })}
                >
                  {board.semesters.map(s => (
                    <option key={s.id} value={s.id}>{s.id} ({s.programme} Sem {s.number}{s.name ? ` - ${s.name}` : ''})</option>
                  ))}
                </select>
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                <div>
                  <label className="mono-label">Internal / Primary Faculty</label>
                  <select
                    className="form-control"
                    value={editingSubject.internal_teacher_id || ''}
                    onChange={e => setEditingSubject({ ...editingSubject, internal_teacher_id: e.target.value || null })}
                  >
                    <option value="">Unassigned</option>
                    {board.teachers.map(t => (
                      <option key={t.id} value={t.id}>{t.name} ({t.id}) — {t.internal ? 'Internal' : 'External'}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="mono-label">External Faculty (Optional)</label>
                  <select
                    className="form-control"
                    value={editingSubject.external_teacher_id || ''}
                    onChange={e => setEditingSubject({ ...editingSubject, external_teacher_id: e.target.value || null })}
                  >
                    <option value="">None / N/A</option>
                    {board.teachers.map(t => (
                      <option key={t.id} value={t.id}>{t.name} ({t.id}) — {t.internal ? 'Internal' : 'External'}</option>
                    ))}
                  </select>
                </div>
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                <div>
                  <label className="mono-label">Class Type</label>
                  <select
                    className="form-control"
                    value={editingSubject.class_type}
                    onChange={e => {
                      const ct = e.target.value as 'theory' | 'practical'
                      setEditingSubject({
                        ...editingSubject,
                        class_type: ct,
                        sessions_per_week: ct === 'practical' ? 1 : (editingSubject.sessions_per_week || 3),
                      })
                    }}
                  >
                    <option value="theory">Theory (Lecture)</option>
                    <option value="practical">Practical (Laboratory)</option>
                  </select>
                </div>
                <div>
                  <label className="mono-label">Weekly Sessions Count (e.g. 2, 2.5, 3)</label>
                  <input
                    type="number"
                    step="0.5"
                    min="0.5"
                    max="10"
                    className="form-control"
                    value={editingSubject.sessions_per_week ?? 3}
                    onChange={e => setEditingSubject({ ...editingSubject, sessions_per_week: parseFloat(e.target.value) || 0.5 })}
                  />
                  <div style={{ fontSize: 10, color: 'var(--ink-soft)', marginTop: 2 }}>
                    Multiples of 0.5: 2 = 4h, 2.5 = 5h (two 2h + one 1h), 3 = 6h.
                  </div>
                </div>
              </div>

              {editingSubject.external_teacher_id && (
                <div>
                  <label className="mono-label">External Teacher Sessions (out of {editingSubject.sessions_per_week ?? 3})</label>
                  <input
                    type="number"
                    step="0.5"
                    min="0"
                    max={editingSubject.sessions_per_week ?? 3}
                    className="form-control"
                    value={editingSubject.external_sessions ?? 0}
                    onChange={e => setEditingSubject({ ...editingSubject, external_sessions: parseFloat(e.target.value) || 0 })}
                  />
                </div>
              )}

              <div>
                <label className="mono-label">Required Room Facility</label>
                <select
                  className="form-control"
                  value={editingSubject.facility || 'none'}
                  onChange={e => setEditingSubject({ ...editingSubject, facility: e.target.value })}
                >
                  <option value="none">Standard / None</option>
                  <option value="blackboard">Blackboard</option>
                  <option value="projector">Projector</option>
                  <option value="both">Both (Blackboard & Projector)</option>
                </select>
              </div>

              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 12 }}>
                <button className="btn btn-ghost btn-sm" onClick={() => { setEditingSubject(null); setEditingSubjectIdx(null); }}>Cancel</button>
                <button
                  className="btn btn-primary btn-sm"
                  onClick={() => {
                    if (!editingSubject.code.trim() || !editingSubject.semester_id) return
                    const updatedSubjects = [...board.subjects]
                    updatedSubjects[editingSubjectIdx] = {
                      ...editingSubject,
                      code: editingSubject.code.trim(),
                      name: editingSubject.name.trim(),
                    }
                    const updatedBoard = {
                      ...board,
                      subjects: updatedSubjects,
                    }
                    setBoard(updatedBoard)
                    autoValidateBoard(updatedBoard)
                    setEditingSubject(null)
                    setEditingSubjectIdx(null)
                    setSaveSuccessMsg(`Updated course subject ${editingSubject.code} (${editingSubject.name})`)
                  }}
                >
                  Save Changes
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ─────────────────────────────────────────────────────────── */}
      {/* MODAL 8: EDIT SEMESTER                                     */}
      {/* ─────────────────────────────────────────────────────────── */}
      {editingSemester && editingSemesterIdx !== null && (
        <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 9999, padding: 16 }}>
          <div style={{ background: 'var(--card-bg)', border: '1px solid var(--line)', borderRadius: 'var(--radius-lg)', boxShadow: 'var(--shadow)', width: '100%', maxWidth: 480, padding: 20 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
              <h3 style={{ fontFamily: 'var(--font-display)', fontSize: 'var(--fs-md)', fontWeight: 700, margin: 0, display: 'flex', alignItems: 'center', gap: 8 }}>
                <Pencil size={16} style={{ color: 'var(--accent)' }} />
                Edit Semester: {editingSemester.id}
              </h3>
              <button className="btn-icon" onClick={() => { setEditingSemester(null); setEditingSemesterIdx(null); }}><X size={16} /></button>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              <div>
                <label className="mono-label">Programme</label>
                <input
                  type="text"
                  className="form-control"
                  value={editingSemester.programme}
                  onChange={e => setEditingSemester({ ...editingSemester, programme: e.target.value })}
                />
              </div>

              <div>
                <label className="mono-label">Semester Number</label>
                <input
                  type="number"
                  min="1"
                  max="10"
                  className="form-control"
                  value={editingSemester.number}
                  onChange={e => setEditingSemester({ ...editingSemester, number: parseInt(e.target.value, 10) || 1 })}
                />
              </div>

              <div>
                <label className="mono-label">Unique Semester ID</label>
                <input
                  type="text"
                  className="form-control"
                  value={editingSemester.id}
                  onChange={e => setEditingSemester({ ...editingSemester, id: e.target.value })}
                />
              </div>

              <div>
                <label className="mono-label">Display Name</label>
                <input
                  type="text"
                  className="form-control"
                  value={editingSemester.name || ''}
                  onChange={e => setEditingSemester({ ...editingSemester, name: e.target.value })}
                />
              </div>

              <div>
                <label className="mono-label">Student Cohort Strength</label>
                <input
                  type="number"
                  min="1"
                  className="form-control"
                  value={editingSemester.strength ?? 60}
                  onChange={e => setEditingSemester({ ...editingSemester, strength: parseInt(e.target.value, 10) || 60 })}
                />
              </div>

              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 12 }}>
                <button className="btn btn-ghost btn-sm" onClick={() => { setEditingSemester(null); setEditingSemesterIdx(null); }}>Cancel</button>
                <button
                  className="btn btn-primary btn-sm"
                  onClick={() => {
                    if (!editingSemester.id.trim()) return
                    const oldId = board.semesters[editingSemesterIdx].id
                    const newId = editingSemester.id.trim()

                    const updatedSemesters = [...board.semesters]
                    updatedSemesters[editingSemesterIdx] = {
                      ...editingSemester,
                      id: newId,
                    }

                    let updatedSubjects = board.subjects
                    if (oldId !== newId) {
                      updatedSubjects = board.subjects.map(sub =>
                        sub.semester_id === oldId ? { ...sub, semester_id: newId } : sub
                      )
                    }

                    const updatedBoard = {
                      ...board,
                      semesters: updatedSemesters,
                      subjects: updatedSubjects,
                    }
                    setBoard(updatedBoard)
                    autoValidateBoard(updatedBoard)
                    setEditingSemester(null)
                    setEditingSemesterIdx(null)
                    setSaveSuccessMsg(`Updated semester ${newId}`)
                  }}
                >
                  Save Changes
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ─────────────────────────────────────────────────────────── */}
      {/* MODAL 9: EDIT ROOM                                         */}
      {/* ─────────────────────────────────────────────────────────── */}
      {editingRoom && editingRoomIdx !== null && (
        <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 9999, padding: 16 }}>
          <div style={{ background: 'var(--card-bg)', border: '1px solid var(--line)', borderRadius: 'var(--radius-lg)', boxShadow: 'var(--shadow)', width: '100%', maxWidth: 440, padding: 20 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
              <h3 style={{ fontFamily: 'var(--font-display)', fontSize: 'var(--fs-md)', fontWeight: 700, margin: 0, display: 'flex', alignItems: 'center', gap: 8 }}>
                <Pencil size={16} style={{ color: 'var(--accent)' }} />
                Edit Room: {editingRoom.number}
              </h3>
              <button className="btn-icon" onClick={() => { setEditingRoom(null); setEditingRoomIdx(null); }}><X size={16} /></button>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              <div>
                <label className="mono-label">Room Number / Identifier</label>
                <input
                  type="text"
                  className="form-control"
                  value={editingRoom.number}
                  onChange={e => setEditingRoom({ ...editingRoom, number: e.target.value })}
                />
              </div>

              <div>
                <label className="mono-label">Room Kind</label>
                <select
                  className="form-control"
                  value={editingRoom.kind}
                  onChange={e => setEditingRoom({ ...editingRoom, kind: e.target.value as 'classroom' | 'lab' })}
                >
                  <option value="classroom">Classroom (Lecture Hall)</option>
                  <option value="lab">Laboratory (Practical Block)</option>
                </select>
              </div>

              <div>
                <label className="mono-label">Facility Provided</label>
                <select
                  className="form-control"
                  value={editingRoom.facility}
                  onChange={e => setEditingRoom({ ...editingRoom, facility: e.target.value })}
                >
                  <option value="blackboard">Blackboard</option>
                  <option value="projector">Projector</option>
                  <option value="both">Both (Blackboard & Projector)</option>
                  <option value="none">Standard / None</option>
                </select>
              </div>

              <div>
                <label className="mono-label">Capacity (Students)</label>
                <input
                  type="number"
                  min="1"
                  className="form-control"
                  value={editingRoom.capacity ?? 60}
                  onChange={e => setEditingRoom({ ...editingRoom, capacity: parseInt(e.target.value, 10) || 60 })}
                />
              </div>

              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 12 }}>
                <button className="btn btn-ghost btn-sm" onClick={() => { setEditingRoom(null); setEditingRoomIdx(null); }}>Cancel</button>
                <button
                  className="btn btn-primary btn-sm"
                  onClick={() => {
                    if (!editingRoom.number.trim()) return
                    const updatedRooms = [...board.rooms]
                    updatedRooms[editingRoomIdx] = {
                      ...editingRoom,
                      number: editingRoom.number.trim(),
                    }
                    const updatedBoard = {
                      ...board,
                      rooms: updatedRooms,
                    }
                    setBoard(updatedBoard)
                    autoValidateBoard(updatedBoard)
                    setEditingRoom(null)
                    setEditingRoomIdx(null)
                    setSaveSuccessMsg(`Updated room ${editingRoom.number}`)
                  }}
                >
                  Save Changes
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ─────────────────────────────────────────────────────────── */}
      {/* MODAL 10: CREATED FACULTY ACCOUNTS MODAL                   */}
      {/* ─────────────────────────────────────────────────────────── */}
      {showAccountsModal && createdAccounts.length > 0 && (
        <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 10000, padding: 16 }}>
          <div style={{ background: 'var(--card-bg)', border: '1px solid var(--line)', borderRadius: 'var(--radius-lg)', boxShadow: 'var(--shadow)', width: '100%', maxWidth: 720, maxHeight: '85vh', overflowY: 'auto', padding: 24, display: 'flex', flexDirection: 'column', gap: 16 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderBottom: '1px solid var(--line)', paddingBottom: 12 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <KeyRound size={22} style={{ color: 'var(--accent)' }} />
                <div>
                  <h3 style={{ margin: 0, fontFamily: 'var(--font-display)', fontSize: 'var(--fs-md)', fontWeight: 700 }}>
                    Teacher Accounts & Passwords Generated
                  </h3>
                  <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--ink-soft)' }}>
                    {createdAccounts.length} user account(s) have been registered and granted the TEACHER role.
                  </div>
                </div>
              </div>
              <button className="btn-icon" onClick={() => setShowAccountsModal(false)}><X size={16} /></button>
            </div>

            <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--ink-soft)' }}>
              These credentials can be shared with teachers so they can log into the institution portal. Make sure to copy and store these securely:
            </div>

            <div style={{ border: '1px solid var(--line)', borderRadius: 'var(--radius)', overflowX: 'auto', maxHeight: 320, overflowY: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 'var(--fs-xs)' }}>
                <thead>
                  <tr style={{ background: 'var(--paper)', borderBottom: '1px solid var(--line)', textAlign: 'left' }}>
                    <th style={{ padding: '8px 12px' }}>Faculty Name</th>
                    <th style={{ padding: '8px 12px' }}>Short Form</th>
                    <th style={{ padding: '8px 12px' }}>Login Email</th>
                    <th style={{ padding: '8px 12px' }}>Password</th>
                  </tr>
                </thead>
                <tbody>
                  {createdAccounts.map((acc, idx) => (
                    <tr key={idx} style={{ borderBottom: '1px solid var(--line)' }}>
                      <td style={{ padding: '8px 12px', fontWeight: 600 }}>{acc.full_name}</td>
                      <td style={{ padding: '8px 12px', fontFamily: 'var(--font-mono)', color: 'var(--accent)', fontWeight: 700 }}>{acc.short_name}</td>
                      <td style={{ padding: '8px 12px', fontFamily: 'var(--font-mono)' }}>{acc.email}</td>
                      <td style={{ padding: '8px 12px', fontFamily: 'var(--font-mono)', fontWeight: 700, color: 'var(--ink)' }}>
                        <code>{acc.password || '••••••••'}</code>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', paddingTop: 8 }}>
              <button
                className="btn btn-ghost btn-sm"
                onClick={() => {
                  const csv = ['Teacher Name,Short Name,Email,Password', ...createdAccounts.map(a => `"${a.full_name}","${a.short_name}","${a.email}","${a.password || ''}"`)].join('\n')
                  downloadCSVFile(`teacher_credentials_${Date.now()}.csv`, csv)
                }}
                style={{ gap: 6 }}
              >
                <Download size={13} /> Export Credentials CSV
              </button>

              <div style={{ display: 'flex', gap: 8 }}>
                <button
                  className="btn btn-primary btn-sm"
                  style={{ gap: 6 }}
                  onClick={() => {
                    const text = createdAccounts.map(a => `Teacher: ${a.full_name} (${a.short_name})\nEmail: ${a.email}\nPassword: ${a.password || ''}\nRole: TEACHER\n`).join('\n')
                    navigator.clipboard.writeText(text)
                    setCopiedAccounts(true)
                    setTimeout(() => setCopiedAccounts(false), 2500)
                  }}
                >
                  <Copy size={13} /> {copiedAccounts ? 'Copied to Clipboard!' : 'Copy All Credentials'}
                </button>
                <button className="btn btn-ghost btn-sm" onClick={() => setShowAccountsModal(false)}>Close</button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
