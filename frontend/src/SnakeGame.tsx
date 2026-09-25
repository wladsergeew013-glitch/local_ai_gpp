import { memo, useCallback, useEffect, useRef, useState } from 'react';

type Cell = { x: number; y: number };
type Direction = 'up' | 'down' | 'left' | 'right';
type Game = { snake: Cell[]; food: Cell; score: number; over: boolean };

const COLUMNS = 14;
const ROWS = 8;
const STEPS: Record<Direction, Cell> = {
  up: { x: 0, y: -1 },
  down: { x: 0, y: 1 },
  left: { x: -1, y: 0 },
  right: { x: 1, y: 0 },
};
const OPPOSITE: Record<Direction, Direction> = {
  up: 'down', down: 'up', left: 'right', right: 'left',
};

function sameCell(a: Cell, b: Cell): boolean {
  return a.x === b.x && a.y === b.y;
}

function nextFood(snake: Cell[]): Cell {
  const free: Cell[] = [];
  for (let y = 0; y < ROWS; y += 1) {
    for (let x = 0; x < COLUMNS; x += 1) {
      const cell = { x, y };
      if (!snake.some((part) => sameCell(part, cell))) free.push(cell);
    }
  }
  return free[Math.floor(Math.random() * free.length)] || snake[0];
}

function newGame(): Game {
  const snake = [{ x: 6, y: 4 }, { x: 5, y: 4 }, { x: 4, y: 4 }];
  return { snake, food: nextFood(snake), score: 0, over: false };
}

function SnakeGame() {
  const [game, setGame] = useState<Game>(newGame);
  const direction = useRef<Direction>('right');
  const nextDirection = useRef<Direction>('right');
  const boardRef = useRef<HTMLDivElement>(null);

  const turn = useCallback((value: Direction) => {
    if (value !== OPPOSITE[direction.current]) nextDirection.current = value;
  }, []);

  const restart = useCallback(() => {
    direction.current = 'right';
    nextDirection.current = 'right';
    setGame(newGame());
    boardRef.current?.focus();
  }, []);

  useEffect(() => {
    boardRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.altKey || event.ctrlKey || event.metaKey) return;
      const target = event.target as HTMLElement | null;
      if (target?.matches('input, textarea, [contenteditable="true"]')) return;
      const key = event.key.toLowerCase();
      const keys: Record<string, Direction> = {
        arrowup: 'up', w: 'up', arrowdown: 'down', s: 'down',
        arrowleft: 'left', a: 'left', arrowright: 'right', d: 'right',
      };
      const selected = keys[key];
      if (selected) {
        event.preventDefault();
        turn(selected);
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [turn]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      setGame((current) => {
        if (current.over) return current;
        direction.current = nextDirection.current;
        const step = STEPS[direction.current];
        const head = current.snake[0];
        const next = {
          x: (head.x + step.x + COLUMNS) % COLUMNS,
          y: (head.y + step.y + ROWS) % ROWS,
        };
        const eating = sameCell(next, current.food);
        const body = eating ? current.snake : current.snake.slice(0, -1);
        if (body.some((part) => sameCell(part, next))) return { ...current, over: true };
        const snake = [next, ...body];
        const won = snake.length === COLUMNS * ROWS;
        return {
          snake,
          food: eating && !won ? nextFood(snake) : current.food,
          score: current.score + (eating ? 1 : 0),
          over: won,
        };
      });
    }, 170);
    return () => window.clearInterval(timer);
  }, []);

  const occupied = new Set(game.snake.map((part) => `${part.x},${part.y}`));
  return (
    <section className="agent-v12-snake" aria-label="Змейка">
      <div className="agent-v12-snake-heading">
        <strong>Змейка · пока готовится ответ</strong>
        <span>Счёт: {game.score}</span>
      </div>
      <div className="agent-v12-snake-content">
        <div ref={boardRef} className="agent-v12-snake-board" tabIndex={0} aria-label="Поле игры. Управление стрелками или клавишами WASD">
          {Array.from({ length: ROWS * COLUMNS }, (_, index) => {
            const x = index % COLUMNS;
            const y = Math.floor(index / COLUMNS);
            const kind = occupied.has(`${x},${y}`) ? 'snake' : game.food.x === x && game.food.y === y ? 'food' : '';
            return <span key={index} className={`agent-v12-snake-cell ${kind}`} />;
          })}
          {game.over && (
            <div className="agent-v12-snake-over">
              <span>Игра окончена</span>
              <button type="button" onClick={restart}>Ещё раз</button>
            </div>
          )}
        </div>
        <div className="agent-v12-snake-controls" aria-label="Управление змейкой">
          <button type="button" className="up" onClick={() => turn('up')} aria-label="Вверх">↑</button>
          <button type="button" className="left" onClick={() => turn('left')} aria-label="Влево">←</button>
          <button type="button" className="down" onClick={() => turn('down')} aria-label="Вниз">↓</button>
          <button type="button" className="right" onClick={() => turn('right')} aria-label="Вправо">→</button>
          <small>Стрелки / WASD</small>
        </div>
      </div>
    </section>
  );
}

export default memo(SnakeGame);
