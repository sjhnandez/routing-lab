import type { ReactNode } from 'react'
import { Reveal } from './Reveal'

interface SectionProps {
  id: string
  index: string
  title: string
  children: ReactNode
}

export function Section({ id, index, title, children }: SectionProps) {
  return (
    <section id={id} className="scroll-mt-20 border-t border-line/40 py-14 sm:py-20">
      <div className="mx-auto max-w-5xl px-5 sm:px-8">
        <Reveal>
          <p className="font-mono text-xs tracking-[0.3em] text-accent">{index}</p>
          <h2 className="mt-2 font-display text-3xl font-bold tracking-tight sm:text-4xl">
            {title}
          </h2>
        </Reveal>
        <div className="mt-8 sm:mt-11">{children}</div>
      </div>
    </section>
  )
}
