// Read actual browser observations; never adjust coordinates or product styles.
async function canvasMetrics(page, selector = 'canvas') {
  return page.evaluate(selector => {
    const canvas = document.querySelector(selector);
    if (!(canvas instanceof HTMLCanvasElement)) throw Error('canvas_metrics_canvas_required');
    const rect = canvas.getBoundingClientRect(), style = getComputedStyle(canvas);
    const number = key => parseFloat(style[key]) || 0;
    // clientWidth excludes borders and includes padding. CSS transforms remain
    // represented separately by border_box (the visual DOMRect).
    return {
      bitmap: {width: canvas.width, height: canvas.height},
      content_box: {
        width: parseFloat(style.width) - (style.boxSizing === 'border-box' ? number('borderLeftWidth') + number('borderRightWidth') + number('paddingLeft') + number('paddingRight') : 0),
        height: parseFloat(style.height) - (style.boxSizing === 'border-box' ? number('borderTopWidth') + number('borderBottomWidth') + number('paddingTop') + number('paddingBottom') : 0),
      },
      border_box: {x:rect.x,y:rect.y,width:rect.width,height:rect.height,right:rect.right,bottom:rect.bottom},
      viewport: {width:innerWidth,height:innerHeight},
      device_pixel_ratio: devicePixelRatio,
    };
  }, selector);
}
module.exports = {canvasMetrics};
