import { Search } from 'lucide-react'
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
  const { catalogPrograms, catalogTeachers, catalogSubjects } = useWorkspaceStore()
  const PROGRAMS = Array.from(new Set(['All', ...catalogPrograms.map(p => p.name), 'B.Tech', 'M.Tech', 'M.Sc']))
  const TEACHERS = Array.from(new Set(catalogTeachers.map(t => t.short_name)))

  return (
    <div className="filter-bar">
      <div className="input-group" style={{ flex: '1 1 180px', maxWidth: 280 }}>
        <Search size={13} className="input-icon" />
        <input
          className="form-control filter-select"
          style={{ paddingLeft: 30, height: 30 }}
          placeholder="Search subject, teacher, room…"
          value={filters.search}
          onChange={e => onChange({ search: e.target.value })}
        />
      </div>

      <select className="filter-select" value={filters.program} onChange={e => onChange({ program: e.target.value })}>
        {PROGRAMS.map(p => <option key={p}>{p}</option>)}
      </select>

      <select className="filter-select" value={filters.semester} onChange={e => onChange({ semester: e.target.value })}>
        {SEMESTERS.map(s => <option key={s}>{s}</option>)}
      </select>

      <select className="filter-select" value={filters.day} onChange={e => onChange({ day: e.target.value })}>
        <option value="">All Days</option>
        {DAYS.map(d => <option key={d}>{d}</option>)}
      </select>

      <input
        className="filter-select"
        style={{ minWidth: 90 }}
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
        style={{ minWidth: 80 }}
        placeholder="Room…"
        value={filters.room}
        onChange={e => onChange({ room: e.target.value })}
      />

      {onDensityChange && (
        <div className="tabs" style={{ marginLeft: 'auto' }}>
          <button className={`tab ${density === 'comfortable' ? 'active' : ''}`} onClick={() => onDensityChange('comfortable')}>Comfortable</button>
          <button className={`tab ${density === 'compact' ? 'active' : ''}`} onClick={() => onDensityChange('compact')}>Compact</button>
        </div>
      )}
    </div>
  )
}
