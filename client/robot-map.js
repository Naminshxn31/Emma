/* Occupancy grid from /api/layout: walls the visitor cannot walk through and
   the minimap's floor plan. Same rows the Python planner uses — one map, not
   a JavaScript copy that drifts. Nothing here moves the robot. */
export class GridMap {
  constructor(layout) {
    this.cell=layout.cell; [this.xmin,this.ymin,this.xmax,this.ymax]=layout.bounds;
    this.rows=layout.rows; this.cols=layout.cols; this.grid=layout.grid;
    this.image=null;
  }
  charAt(x,y) {
    const col=Math.floor((x-this.xmin)/this.cell), row=Math.floor((y-this.ymin)/this.cell);
    if(row<0||row>=this.rows||col<0||col>=this.cols) return ' ';
    return this.grid[row][col];
  }
  /* Layout units (x, y with y away from the entrance), not scene z. */
  blocked(x,y,radius=.2) {
    for(const [dx,dy] of [[0,0],[radius,0],[-radius,0],[0,radius],[0,-radius]]) {
      const c=this.charAt(x+dx,y+dy);
      if(c!=='.'&&c!=='D') return true;
    }
    return false;
  }
  /* Cached plan for the minimap: dark walls, pale floor, transparent void. */
  plan() {
    if(this.image) return this.image;
    const c=document.createElement('canvas'); c.width=this.cols; c.height=this.rows;
    const ctx=c.getContext('2d'), img=ctx.createImageData(this.cols,this.rows);
    for(let r=0;r<this.rows;r++) for(let col=0;col<this.cols;col++) {
      const ch=this.grid[r][col], i=(r*this.cols+col)*4;
      const rgb=ch==='#'?[214,226,232]:ch==='D'?[120,237,202]:ch==='.'?[52,74,88]:null;
      if(!rgb) continue;
      img.data[i]=rgb[0]; img.data[i+1]=rgb[1]; img.data[i+2]=rgb[2]; img.data[i+3]=ch==='.'?170:255;
    }
    ctx.putImageData(img,0,0);
    this.image=c; return c;
  }
}
