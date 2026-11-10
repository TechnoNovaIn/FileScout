import React, { useEffect, useRef } from 'react';
import styles from './ProgressLog.module.css';

export default function ProgressLog({ lines, searching }) {
  const bottomRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [lines]);

  return (
    <div className={styles.panel}>
      <div className={styles.header}>
        <span className={styles.title}>SCAN LOG</span>
        {searching && <span className={styles.live}>● LIVE</span>}
      </div>
      <div className={styles.log}>
        {lines.map((line, i) => {
          const isFound = line.startsWith('FOUND:');
          const isSkip  = line.startsWith('SKIP:');
          const isNone  = line.startsWith('NONE:');
          return (
            <div
              key={i}
              className={`${styles.line} ${isFound ? styles.found : ''} ${isSkip ? styles.skip : ''} ${isNone ? styles.none : ''}`}
            >
              <span className={styles.prefix}>
                {isFound ? '✓' : isSkip ? '○' : isNone ? '–' : '›'}
              </span>
              <span className={styles.text}>{line.replace(/^(FOUND|SKIP|NONE|Scanning):?\s*/, '')}</span>
            </div>
          );
        })}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
