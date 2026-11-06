// Utility functions for teacher short forms, catalog mappings, and display formatting

export const DEMO_TEACHER_SHORT_MAP: Record<string, string> = {
  I01: 'AR',
  I02: 'BI',
  I03: 'CD',
  I04: 'DM',
  I05: 'EK',
  I06: 'FN',
  I07: 'GS',
  I08: 'HJ',
  I09: 'IR',
  I10: 'JV',
  I11: 'GM',
  I12: 'AD',
  I13: 'TD',
  I14: 'DK',
  I15: 'AG',
  I16: 'SGC',
  I17: 'SR',
  I18: 'SD',
  I19: 'DC',
  I21: 'RKP',
  E01: 'KP',
  E02: 'LG',
  E03: 'MS',
  E04: 'NB',
}

export const KNOWN_SUBJECT_TEACHER_MAP: Record<string, string> = {
  'research methodology': 'DC',
  'discrete mathematics': 'SCh',
  'software engineering': 'NC',
  'compiler design': 'SKS',
  'deep learning': 'SKS',
  'digital logic': 'SK',
  'dbms': 'SC',
  'database systems': 'SC',
  'algorithmic graph theory': 'SN',
  'mathematical foundations of computer science': 'SGC',
  'microprocessor & microcontroller': 'RD',
  'microprocessor': 'RD',
  'introduction to data mining': 'RD',
  'ai & machine learning': 'AD',
  'artificial intelligence': 'AG',
  'ai lab': 'AG',
  'aiml lab': 'AD',
  'cg lab': 'GM',
  'computer graphics': 'GM',
  'environment systems & internet of things': 'TD',
  'environmental science': 'PBn',
  'soft computing lab': 'SR',
  'soft computing': 'SR',
  'adbms lab': 'NC',
  'advance dbms': 'NC',
  'wireless & mobile computing': 'SC',
  'external practical training': 'EPT',
  'data structure': 'PB',
  'data structures': 'PB',
  'data structure lab': 'PB',
  'advanced algorithms': 'PB',
  'computer networks': 'SK',
  'cn lab': 'SK',
  'cloud computing': 'JV',
  'linear algebra': 'DM',
  'statistics': 'EK',
  'numerical methods': 'FN',
  'optimisation': 'HJ',
  'problem solving techniques': 'RKP',
  'vlsi design': 'RKP',
  'english for research paper writing': 'SD',
  'image processing & pattern recognition': 'DK',
}

/**
 * Derive academic short form (initials) from a teacher's full name.
 * e.g. "Dr. Debesh Choudhury" -> "DC", "Prof. C. Das" -> "CD", "Dr. A. Rao" -> "AR"
 */
export function deriveTeacherShortForm(fullName: string, fallbackId = ''): string {
  const cleanFallback = (fallbackId || '').trim()
  const isOpaque = /^[IE]\d+$/i.test(cleanFallback)

  if (!fullName || !fullName.trim()) {
    if (cleanFallback && !isOpaque) return cleanFallback.toUpperCase()
    if (cleanFallback && DEMO_TEACHER_SHORT_MAP[cleanFallback.toUpperCase()]) {
      return DEMO_TEACHER_SHORT_MAP[cleanFallback.toUpperCase()]
    }
    return cleanFallback ? cleanFallback.toUpperCase() : 'TCH'
  }

  let name = fullName.replace(/\(.*?\)/g, '').trim()
  name = name.replace(/\b(dr|prof|professor|mr|ms|mrs|er|shri|smt|guest|faculty)\b\.?/gi, '').trim()
  const parts = name.split(/[\s._\-]+/).filter(Boolean)

  if (parts.length === 0) {
    if (cleanFallback && !isOpaque) return cleanFallback.toUpperCase()
    if (cleanFallback && DEMO_TEACHER_SHORT_MAP[cleanFallback.toUpperCase()]) {
      return DEMO_TEACHER_SHORT_MAP[cleanFallback.toUpperCase()]
    }
    return 'TCH'
  }

  if (parts.length === 1) {
    return parts[0].slice(0, 3).toUpperCase()
  }

  return parts.map(p => p[0].toUpperCase()).join('')
}

/**
 * Resolve teacher display string into a clean short form.
 * Guarantees that raw IDs like I01, I02.. I19, E02 are NEVER displayed to the user.
 */
export function formatTeacherShortName(
  teacherVal?: string,
  subjectName?: string,
  catalogTeachers?: { id?: number; short_name: string; full_name: string }[]
): string {
  const val = (teacherVal || '').trim()
  const isOpaque = /^[IE]\d+$/i.test(val)

  // 1. If it's already a clean short form (not I01..I19, E01..E04)
  if (val && !isOpaque && val.length <= 5 && val === val.toUpperCase()) {
    return val
  }

  // 2. Check catalog teachers by short_name or full_name
  if (catalogTeachers && catalogTeachers.length > 0) {
    if (!isOpaque && val) {
      const match = catalogTeachers.find(
        t => t.short_name?.toLowerCase() === val.toLowerCase() || t.full_name?.toLowerCase() === val.toLowerCase()
      )
      if (match?.short_name) return match.short_name
    }
  }

  // 3. Check known subject mapping
  const subKey = (subjectName || '').trim().toLowerCase()
  if (subKey && KNOWN_SUBJECT_TEACHER_MAP[subKey]) {
    return KNOWN_SUBJECT_TEACHER_MAP[subKey]
  }

  // 4. Check known demo ID map (I01..I19, E01..E04)
  if (isOpaque && DEMO_TEACHER_SHORT_MAP[val.toUpperCase()]) {
    return DEMO_TEACHER_SHORT_MAP[val.toUpperCase()]
  }

  // 5. Derive from full name
  if (val && !isOpaque) {
    return deriveTeacherShortForm(val)
  }

  return val || 'TCH'
}
