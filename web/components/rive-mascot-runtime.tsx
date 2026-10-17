'use client';
import { useCallback, useEffect, useState } from 'react';
import { Alignment, EventType, Fit, Layout, RuntimeLoader, useRive } from '@rive-app/react-canvas';
import { AudliMascot } from './audli-mascot';
import { startMascot } from '../lib/mascot-celebration';

RuntimeLoader.setWasmUrl('/audli/rive.wasm');
RuntimeLoader.setWasmFallbackUrl(null);
const layout = new Layout({ fit: Fit.Contain, alignment: Alignment.Center });

function MascotCanvas({ identity, celebrate, visible, onReady, onFailure }: {
  identity: string; celebrate: boolean; visible: boolean; onReady: () => void; onFailure: () => void;
}) {
  const { rive, RiveComponent } = useRive({
    src: '/audli/audli-mascot.riv',
    artboard: 'AudliMascot',
    stateMachine: 'AudliMascotMachine',
    autoplay: false,
    autoBind: false,
    layout,
    shouldDisableRiveListeners: true,
    enableRiveAssetCDN: false,
    onLoadError: onFailure,
  });
  useEffect(() => {
    if (!rive) return;
    let cancel = () => {};
    try {
      // Reject an accidental earlier export rather than render a clipped mascot.
      if (rive.artboardWidth !== 800 || rive.artboardHeight !== 800) throw new Error('Unexpected mascot artboard');
      startMascot(rive, identity, celebrate, undefined, undefined, fire => {
        // Enter the default state before firing: a trigger sent before the first
        // advance is consumed during initialization instead of celebrating.
        const initialized = () => {
          rive.off(EventType.Advance, initialized);
          fire();
        };
        rive.on(EventType.Advance, initialized);
        cancel = () => rive.off(EventType.Advance, initialized);
      });
      onReady();
    } catch { onFailure(); }
    return () => cancel();
  }, [rive, identity, celebrate, onReady, onFailure]);
  // useRive owns cleanup and device-pixel-ratio-aware canvas resizing.
  return <div className="mascot-overlay" style={{ visibility: visible ? 'visible' : 'hidden' }}><RiveComponent aria-hidden="true"/></div>;
}

export default function RiveMascotRuntime({ identity, celebrate }: { identity: string; celebrate: boolean }) {
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);
  const onReady = useCallback(() => setReady(true), []);
  const onFailure = useCallback(() => setFailed(true), []);
  useEffect(() => {
    if (ready || failed) return;
    const timeout = setTimeout(onFailure, 10_000);
    return () => clearTimeout(timeout);
  }, [ready, failed, onFailure]);
  return <AudliMascot state="Success" staticPose artworkHidden={ready && !failed}>
    {!failed && <MascotCanvas identity={identity} celebrate={celebrate} visible={ready} onReady={onReady} onFailure={onFailure}/>}
  </AudliMascot>;
}
