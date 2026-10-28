import { useMemo, useEffect } from 'react'
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
  const { catalogPrograms, catalogTeachers, loadCatalog } = useWorkspaceStore()

  useEffect(() => {
    if (!catalogTeachers.length || !catalogPrograms.length) {
      loadCatalog()
    }
  }, [catalogTeachers.length, catalogPrograms.length, loadCatalog])

  const PROGRAMS = useMemo(() => {
    return Array.from(new Set(['All', ...catalogPrograms.map(p => p.name), 'B.Tech', 'M.Tech', 'M.Sc']))
  }, [catalogPrograms])

  const TEACHERS = useMemo(() => {
    const list: { short_name: string; full_name?: string }[] = []
    const seen = new Set<string>()

    const defaults: Record<string, string> = {
      'SKS': 'Sanjit Kumar Setua',
      'RKP': 'Rajat Kumar Pal',
      'SC': 'Sankhayan Choudhury',
      'NC': 'Nabendu Chaki',
      'RD': 'Rajib Kumar Das',
      'PB': 'Pritha Banerjee',
      'SK': 'Sunirmal Khatua',
      'SR': 'Somnath Roy',
      'RS': 'Rajesh Sharma',
      'DC': 'Debasish Chakraborty',
      'SGC': 'Subhashis Gangopadhyay',
      'VP': 'Visiting Professor',
    }

    for (const t of catalogTeachers) {
      if (t.short_name && !seen.has(t.short_name)) {
        seen.add(t.short_name)
        list.push({ short_name: t.short_name, full_name: t.full_name })
      }
    }

    for (const [short, full] of Object.entries(defaults)) {
      if (!seen.has(short)) {
        seen.add(short)
        list.push({ short_name: short, full_name: full })
      }
    }

    return list.sort((a, b) => a.short_name.localeCompare(b.short_name))
  }, [catalogTeachers])

  const hasActiveFilters = Boolean(
    (filters.program && filters.program !== 'All') ||
    (filters.semester && filters.semester !== 'All') ||
    filters.day ||
    filters.teacher
  )

  const clearFilters = () => {
    onChange({ search: '', program: 'All', semester: 'All', day: '', teacher: '', room: '' })
  }

  return (
    <div className="filter-bar">
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
          value={filters.teacher}
          onChange={e => onChange({ teacher: e.target.value })}
          title="Filter by Teacher"
        >
          <option value="">All Teachers</option>
          {TEACHERS.map(t => (
            <option key={t.short_name} value={t.short_name}>
              {t.full_name ? `${t.short_name} (${t.full_name})` : t.short_name}
            </option>
          ))}
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
