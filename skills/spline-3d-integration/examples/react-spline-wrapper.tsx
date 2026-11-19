import React, { Suspense, lazy, useState, useRef, useEffect, forwardRef, useImperativeHandle } from 'react';
import type { Application } from '@splinetool/runtime';

// Lazy load the Spline canvas component to prevent bloating initial bundles
const SplineCanvas = lazy(() => import('@splinetool/react-spline'));

export interface SplineWrapperRef {
  getApplication: () => Application | null;
  emitEvent: (eventName: string, targetName: string) => void;
}

export interface ReactSplineWrapperProps {
  /** Public URL to .splinecode scene */
  scene: string;
  /** Optional custom CSS classes for the container */
  className?: string;
  /** Custom aspect ratio or height styles */
  style?: React.CSSProperties;
  /** Callback when the Spline scene is ready */
  onLoad?: (app: Application) => void;
  /** Callback on object click */
  onMouseDown?: (e: any) => void;
  /** Callback on object hover */
  onMouseHover?: (e: any) => void;
  /** Whether to only load when the container enters the viewport */
  lazy?: boolean;
  /** Custom fallback loading element */
  fallback?: React.ReactNode;
}

export const ReactSplineWrapper = forwardRef<SplineWrapperRef, ReactSplineWrapperProps>(
  (
    {
      scene,
      className = '',
      style,
      onLoad,
      onMouseDown,
      onMouseHover,
      lazy = true,
      fallback,
    },
    ref
  ) => {
    const [isVisible, setIsVisible] = useState(!lazy);
    const [isLoading, setIsLoading] = useState(true);
    const [hasError, setHasError] = useState(false);
    const containerRef = useRef<HTMLDivElement>(null);
    const appRef = useRef<Application | null>(null);

    // Viewport IntersectionObserver
    useEffect(() => {
      if (!lazy || isVisible) return;
      const el = containerRef.current;
      if (!el) return;

      const observer = new IntersectionObserver(
        ([entry]) => {
          if (entry.isIntersecting) {
            setIsVisible(true);
            observer.disconnect();
          }
        },
        { rootMargin: '150px' }
      );

      observer.observe(el);
      return () => observer.disconnect();
    }, [lazy, isVisible]);

    // Cleanup WebGL on unmount
    useEffect(() => {
      return () => {
        if (appRef.current && typeof appRef.current.dispose === 'function') {
          appRef.current.dispose();
          appRef.current = null;
        }
      };
    }, []);

    // Expose methods via ref
    useImperativeHandle(ref, () => ({
      getApplication: () => appRef.current,
      emitEvent: (eventName: string, targetName: string) => {
        appRef.current?.emitEvent(eventName as any, targetName);
      },
    }));

    function handleLoad(app: Application) {
      appRef.current = app;
      setIsLoading(false);
      onLoad?.(app);
    }

    const defaultFallback = (
      <div className="absolute inset-0 flex flex-col items-center justify-center bg-neutral-900/60 backdrop-blur-sm transition-opacity duration-300">
        <div className="w-10 h-10 border-4 border-indigo-500/30 border-t-indigo-500 rounded-full animate-spin mb-3" />
        <span className="text-xs uppercase tracking-wider text-neutral-400 font-medium">
          Loading 3D Environment...
        </span>
      </div>
    );

    if (hasError) {
      return (
        <div
          ref={containerRef}
          className={`relative flex items-center justify-center bg-neutral-950 text-neutral-500 border border-neutral-800 rounded-xl p-6 ${className}`}
          style={style}
        >
          <p className="text-sm">Unable to render 3D scene. Please check your network or device settings.</p>
        </div>
      );
    }

    return (
      <div
        ref={containerRef}
        className={`relative overflow-hidden ${className}`}
        style={style}
      >
        {isLoading && (fallback || defaultFallback)}

        {isVisible && (
          <Suspense fallback={null}>
            <SplineCanvas
              scene={scene}
              onLoad={handleLoad}
              onSplineMouseDown={onMouseDown}
              onSplineMouseHover={onMouseHover}
            />
          </Suspense>
        )}
      </div>
    );
  }
);

ReactSplineWrapper.displayName = 'ReactSplineWrapper';
export default ReactSplineWrapper;
