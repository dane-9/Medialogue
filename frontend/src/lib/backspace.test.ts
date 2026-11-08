// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { blockBackspaceNavigation } from './backspace'

let stop: (() => void) | undefined
afterEach(() => { stop?.(); document.body.innerHTML = '' })

function press(target: Element, key = 'Backspace') {
  const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true })
  target.dispatchEvent(event)
  return event.defaultPrevented
}

describe('blockBackspaceNavigation', () => {
  it('cancels Backspace outside text fields, so the browser does not go Back', () => {
    stop = blockBackspaceNavigation()
    document.body.innerHTML = '<button>Tab</button><input type="checkbox"><input readonly value="x">'
    expect(press(document.body)).toBe(true)
    expect(press(document.querySelector('button')!)).toBe(true)
    expect(press(document.querySelector('input[type=checkbox]')!)).toBe(true)
    expect(press(document.querySelector('input[readonly]')!)).toBe(true)
  })

  it('leaves Backspace alone where it deletes text, and other keys everywhere', () => {
    stop = blockBackspaceNavigation()
    document.body.innerHTML = '<input><input type="search"><textarea></textarea><div contenteditable="true"></div>'
    for (const element of document.querySelectorAll('input, textarea')) expect(press(element)).toBe(false)
    const editable = document.querySelector('[contenteditable]') as HTMLElement
    Object.defineProperty(editable, 'isContentEditable', { value: true })
    expect(press(editable)).toBe(false)
    expect(press(document.body, 'Delete')).toBe(false)
  })
})
