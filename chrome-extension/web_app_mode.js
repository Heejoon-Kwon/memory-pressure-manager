// Reports only display mode. It never reads page text, inputs, cookies or storage.
chrome.runtime.sendMessage({type:'web-app-mode', standalone:
  ['standalone','minimal-ui','window-controls-overlay'].some(mode => matchMedia('(display-mode: '+mode+')').matches)
}).catch(() => {});
