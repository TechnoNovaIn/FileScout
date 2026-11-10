import React from 'react';
import styles from './OcrQualitySelector.module.css';

const QUALITY_OPTIONS = [
  { value: 'fast', label: 'Fast' },
  { value: 'balanced', label: 'Balanced' },
  { value: 'best', label: 'Best' },
];

export default function OcrQualitySelector({ value, onChange }) {
  return (
    <div className={styles.wrap}>
      <label className={styles.label}>OCR</label>
      <select
        className={styles.select}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        {QUALITY_OPTIONS.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
    </div>
  );
}
