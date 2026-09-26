import { useState } from 'react'
import {
  Plus, Trash2, XCircle, ArrowRight, ArrowLeftRight, RefreshCw,
  Clock, User, MapPin, AlignLeft, Zap, CheckSquare, ChevronDown,
  Sparkles, BookOpen, GraduationCap, FlaskConical, CalendarOff,
} from 'lucide-react'
import ActionForm from './ActionForm'
import SmartScheduleModal from './SmartScheduleModal'
import CatalogManager from '../catalog/CatalogManager'
import BusySlotsManager from '../busyslots/BusySlotsManager'

const QUICK_ACTIONS = [
  { id: 'ADD_CLASS',       icon: <Plus size={14} />,           label: 'Add Class',      cls: 'quick' },
  { id: 'CANCEL_CLASS',    icon: <XCircle size={14} />,        label: 'Cancel Class',   cls: 'danger' },
  { id: 'MOVE_CLASS',      icon: <ArrowRight size={14} />,     label: 'Move Class',     cls: 'quick' },
  { id: 'SWAP_CLASSES',    icon: <ArrowLeftRight size={14} />, label: 'Swap Classes',   cls: 'quick' },
]

const MORE_ACTIONS = [
  { id: 'REMOVE_CLASS',       icon: <Trash2 size={13} />,      label: 'Remove Class' },
  { id: 'INTERCHANGE_CLASSES',icon: <RefreshCw size={13} />,   label: 'Interchange' },
  { id: 'EXTEND_CLASS',       icon: <Clock size={13} />,       label: 'Extend Class' },
  { id: 'CHANGE_TEACHER',     icon: <User size={13} />,        label: 'Change Teacher' },
  { id: 'CHANGE_ROOM',        icon: <MapPin size={13} />,      label: 'Change Room' },
  { id: 'CHANGE_TIME',        icon: <Clock size={13} />,       label: 'Change Time' },
  { id: 'REPLACE_CLASS',      icon: <AlignLeft size={13} />,   label: 'Replace Class' },
  { id: 'OPTIMIZE_TIMETABLE', icon: <Zap size={13} />,         label: 'Optimize' },
  { id: 'MANAGE_AVAILABILITY',icon: <CalendarOff size={13} />, label: 'Busy Slots (Availability)' },
]

const CATALOG_ACTIONS = [
  { id: 'ADD_CATALOG_BUNDLE', icon: <FlaskConical size={13} />,  label: '✨ Bundle Setup' },
  { id: 'ADD_CATALOG_TEACHER', icon: <GraduationCap size={13} />, label: '+ New Teacher' },
  { id: 'ADD_CATALOG_SUBJECT', icon: <BookOpen size={13} />,      label: '+ New Subject' },
  { id: 'ADD_CATALOG_PROGRAM', icon: <FlaskConical size={13} />,  label: '+ New Program' },
]

const VALIDATE_ACTION = { id: 'VALIDATE_TIMETABLE', icon: <CheckSquare size={14} />, label: 'Validate', cls: '' }

export default function ActionPanel() {
  const [openAction, setOpenAction] = useState<string | null>(null)
  const [moreOpen, setMoreOpen] = useState(false)
  const [smartOpen, setSmartOpen] = useState(false)

  return (
    <>
      <div className="action-panel">
        {/* Smart Schedule — visually distinct hero button */}
        <button
          className="action-btn"
          style={{ background: 'linear-gradient(135deg, var(--clr-primary), #7c3aed)', color: '#fff', fontWeight: 700, border: 'none', gap: 5 }}
          onClick={() => setSmartOpen(true)}
          title="Let the system auto-pick the best time slot and room"
        >
          <Sparkles size={14} /> Smart Schedule
        </button>

        <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--clr-text-3)', fontWeight: 600, marginRight: 4, letterSpacing: '0.05em', textTransform: 'uppercase' }}>
          Quick:
        </span>
        {QUICK_ACTIONS.map(a => (
          <button key={a.id} className={`action-btn ${a.cls}`} onClick={() => setOpenAction(a.id)}>
            {a.icon} {a.label}
          </button>
        ))}

        <div style={{ position: 'relative' }}>
          <button className="action-btn" onClick={() => setMoreOpen(o => !o)}>
            More <ChevronDown size={12} />
          </button>
          {moreOpen && (
            <div style={{
              position: 'absolute', top: 'calc(100% + 6px)', left: 0, zIndex: 30,
              background: 'var(--clr-bg-3)', border: '1px solid var(--clr-border)',
              borderRadius: 'var(--radius-lg)', padding: '6px',
              display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 4, minWidth: 280,
              boxShadow: 'var(--shadow)',
            }}>
              {MORE_ACTIONS.map(a => (
                <button key={a.id} className="action-btn" style={{ justifyContent: 'flex-start' }}
                  onClick={() => { setOpenAction(a.id); setMoreOpen(false) }}>
                  {a.icon} {a.label}
                </button>
              ))}
              {/* Catalog section */}
              <div style={{ gridColumn: '1/-1', borderTop: '1px solid var(--clr-border-soft)', margin: '4px 0', padding: '4px 0 0', fontSize: '10px', color: 'var(--clr-text-3)', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.05em' }}>Catalog</div>
              {CATALOG_ACTIONS.map(a => (
                <button key={a.id} className="action-btn" style={{ justifyContent: 'flex-start', color: 'var(--clr-primary)' }}
                  onClick={() => { setOpenAction(a.id); setMoreOpen(false) }}>
                  {a.icon} {a.label}
                </button>
              ))}
            </div>
          )}
        </div>

        <div style={{ marginLeft: 'auto' }}>
          <button className="action-btn" onClick={() => setOpenAction(VALIDATE_ACTION.id)}>
            {VALIDATE_ACTION.icon} {VALIDATE_ACTION.label}
          </button>
        </div>
      </div>

      {openAction && !openAction.startsWith('ADD_CATALOG') && (
        <ActionForm
          actionType={openAction}
          onClose={() => setOpenAction(null)}
        />
      )}

      {openAction && openAction.startsWith('ADD_CATALOG') && (
        <div className="modal-overlay" onClick={e => e.target === e.currentTarget && setOpenAction(null)}>
          <div className="modal" style={{ maxWidth: 900, width: '100%' }}>
            <div className="modal-header">
              <h2 className="modal-title">Catalog Manager</h2>
              <button className="btn-icon" onClick={() => setOpenAction(null)}><XCircle size={18} /></button>
            </div>
            <div style={{ padding: 'var(--sp-4)', maxHeight: '75vh', overflowY: 'auto' }}>
              <CatalogManager 
                initialTab={openAction === 'ADD_CATALOG_BUNDLE' ? 'Bundle Setup' : openAction === 'ADD_CATALOG_TEACHER' ? 'Teachers' : openAction === 'ADD_CATALOG_SUBJECT' ? 'Subjects' : 'Programs'} 
                onClose={() => setOpenAction(null)}
              />
            </div>
          </div>
        </div>
      )}

      {openAction === 'MANAGE_AVAILABILITY' && (
        <div className="modal-overlay" onClick={e => e.target === e.currentTarget && setOpenAction(null)}>
          <div className="modal" style={{ maxWidth: 900, width: '100%' }}>
            <div className="modal-header">
              <h2 className="modal-title">Faculty Availability (Busy Slots)</h2>
              <button className="btn-icon" onClick={() => setOpenAction(null)}><XCircle size={18} /></button>
            </div>
            <div style={{ padding: 'var(--sp-4)', maxHeight: '75vh', overflowY: 'auto' }}>
               <BusySlotsManager />
            </div>
          </div>
        </div>
      )}

      {smartOpen && <SmartScheduleModal onClose={() => setSmartOpen(false)} />}
    </>
  )
}

