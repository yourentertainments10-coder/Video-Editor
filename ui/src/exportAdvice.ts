export interface Advice {
  code: string;
  message: string;
  suggest?: string;
}

export function exportAdvice(assets: Array<{ kind: string; width: number | null; height: number | null }>, profile: { name: string; width: number; height: number }): Advice[] {
  const advice: Advice[] = [];

  // Filter to video assets only
  const videoAssets = assets.filter(a => a.kind === 'video' && a.width !== null && a.height !== null);

  if (videoAssets.length === 0) {
    return advice;
  }

  // Orientation advice: check if most clips are vertical vs horizontal
  const verticalClips = videoAssets.filter(a => a.height! > a.width!).length;
  const horizontalClips = videoAssets.filter(a => a.width! > a.height!).length;
  const isMostlyVertical = verticalClips > horizontalClips;
  const isMostlyHorizontal = horizontalClips > verticalClips;

  // Profile orientation
  const isProfileLandscape = profile.width > profile.height;
  const isProfilePortrait = profile.height > profile.width;

  // Suggest orientation change if mismatch
  if (isMostlyVertical && isProfileLandscape) {
    advice.push({
      code: 'orientation',
      message: 'Most clips are vertical (portrait) but export is landscape',
      suggest: 'reels_9x16'
    });
  } else if (isMostlyHorizontal && isProfilePortrait) {
    advice.push({
      code: 'orientation',
      message: 'Most clips are horizontal (landscape) but export is vertical',
      suggest: 'youtube_1080p'
    });
  }

  // Low resolution advice: check if clips are much smaller than export
  const maxClipWidth = Math.max(...videoAssets.map(a => a.width!));
  const maxClipHeight = Math.max(...videoAssets.map(a => a.height!));

  // Suggest if clips are less than 50% of export resolution in either dimension
  if (maxClipWidth < profile.width * 0.5 || maxClipHeight < profile.height * 0.5) {
    advice.push({
      code: 'low_resolution',
      message: `Clips are ${maxClipWidth} px vs ${profile.width} px export — they will look soft`
    });
  }

  return advice;
}