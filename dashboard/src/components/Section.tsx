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
    <section id={id} className="scroll-mt-16 pt-14 sm:pt-[4.125rem]">
      <div className="mx-auto max-w-[75rem] px-5 sm:px-8">
        <Reveal>
          <div className="flex items-baseline gap-4 border-t border-ink pt-3">
            <p className="font-mono text-xs text-dim">{index}</p>
            <h2 className="font-display text-3xl font-light tracking-tight sm:text-[2.5rem]">
              {title}
            </h2>
          </div>
        </Reveal>
        <div className="mt-6 sm:mt-8">{children}</div>
      </div>
    </section>
  )
}
