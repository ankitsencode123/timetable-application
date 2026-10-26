import { Search, X } from 'lucide-react'
import type { TimetableFilters } from '../../types'
import { SEMESTERS, DAYS } from '../../types'
import { useWorkspaceStore } from '../../store'

interface Props {
  filters: TimetableFilters
  onChange: (f: Partial<TimetableFilters>) => void
  density?: 'comfortable' | 'compact'
  onDensityChange?: (d: 'comfortable' | 'compact') => void
}

export default function FilterBar({ filters, onChange, density, onDensityChange }: Props) {
  const { catalogPrograms, catalogTeachers } = useWorkspaceStore()
  const PROGRAMS = Array.from(new Set(['All', ...catalogPrograms.map(p => p.name), 'B.Tech', 'M.Tech', 'M.Sc']))
  const TEACHERS = Array.from(new Set(catalogTeachers.map(t => t.short_name)))

  const hasActiveFilters = Boolean(
    filters.search ||
    (filters.program && filters.program !== 'All') ||
    (filters.semester && filters.semester !== 'All') ||
    filters.day ||
    filters.teacher ||
    filters.room
  )

  const clearFilters = () => {
    onChange({ search: '', program: 'All', semester: 'All', day: '', teacher: '', room: '' })
  }

  return (
    <div className="filter-bar">
      <div className="filter-search-wrap input-group">
        <Search
          size={14}
          className="input-icon"
          style={{
            position: 'absolute',
            left: 10,
            top: '50%',
            transform: 'translateY(-50%)',
            color: 'var(--ink-soft)',
            pointerEvents: 'none',
            zIndex: 2,
          }}
        />
        <input
          className="form-control filter-select filter-search-input"
          placeholder="Search subject, teacher, room…"
          value={filters.search}
          onChange={e => onChange({ search: e.target.value })}
        />
        {filters.search && (
          <button
            type="button"
            className="filter-clear-search-btn"
            onClick={() => onChange({ search: '' })}
            title="Clear search"
          >
            <X size={12} />
          </button>
        )}
      </div>

      <div className="filter-controls-group">
        <select
          className="filter-select"
          value={filters.program}
          onChange={e => onChange({ program: e.target.value })}
          title="Filter by Program"
        >
          {PROGRAMS.map(p => <option key={p} value={p}>{p === 'All' ? 'All Programs' : p}</option>)}
        </select>

        <select
          className="filter-select"
          value={filters.semester}
          onChange={e => onChange({ semester: e.target.value })}
          title="Filter by Semester"
        >
          {SEMESTERS.map(s => <option key={s} value={s}>{s === 'All' ? 'All Semesters' : s}</option>)}
        </select>

        <select
          className="filter-select"
          value={filters.day}
          onChange={e => onChange({ day: e.target.value })}
          title="Filter by Day"
        >
          <option value="">All Days</option>
          {DAYS.map(d => <option key={d} value={d}>{d}</option>)}
        </select>

        <input
          className="filter-select"
          list="filter-teachers"
          placeholder="Teacher…"
          value={filters.teacher}
          onChange={e => onChange({ teacher: e.target.value })}
        />
        <datalist id="filter-teachers">
          {TEACHERS.map(t => <option key={t} value={t} />)}
        </datalist>

        <input
          className="filter-select"
          placeholder="Room…"
          value={filters.room}
          onChange={e => onChange({ room: e.target.value })}
        />

        {hasActiveFilters && (
          <button
            type="button"
            className="filter-reset-btn"
            onClick={clearFilters}
            title="Reset all filters"
          >
            Reset
          </button>
        )}
      </div>

      {onDensityChange && (
        <div className="tabs filter-density-tabs" style={{ marginLeft: 'auto' }}>
          <button className={`tab ${density === 'comfortable' ? 'active' : ''}`} onClick={() => onDensityChange('comfortable')}>Comfortable</button>
          <button className={`tab ${density === 'compact' ? 'active' : ''}`} onClick={() => onDensityChange('compact')}>Compact</button>
        </div>
      )}
    </div>
  )
}
