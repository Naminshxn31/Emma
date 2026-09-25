-- Smooth direction before posing the curved head mesh. No audio/app connection.
type Gaze = {
    lookX: Property<number>?, lookY: Property<number>?,
    follow: Input<number>, poseX: Input<number>, poseY: Input<number>,
    x: number, y: number, targetX: number, targetY: number,
    size: Vector,
}

function init(self: Gaze, context: Context): boolean
    local vm = context:viewModel()
    if vm ~= nil then
        self.lookX = vm:getNumber('lookX')
        self.lookY = vm:getNumber('lookY')
    end
    return true
end

function resize(self: Gaze, size: Vector)
    self.size = size
end

function pointerMove(self: Gaze, event: PointerEvent)
    -- Unclaimed transparent layouts can receive the last outside move rather
    -- than a dedicated exit callback. Handle that path without capturing clicks.
    if event.position.x < 0 or event.position.y < 0
        or event.position.x > self.size.x or event.position.y > self.size.y then
        self.targetX, self.targetY = 0, 0
        return
    end
    self.targetX = math.clamp((event.position.x - 330) / 360, -1, 1)
    self.targetY = math.clamp((event.position.y - 295) / 340, -1, 1)
end

function pointerExit(self: Gaze, event: PointerEvent)
    self.targetX, self.targetY = 0, 0
end

function advance(self: Gaze, seconds: number): boolean
    local follow = math.clamp(self.follow, 0, 1)
    local targetX = self.targetX * follow + self.poseX * (1-follow)
    local targetY = self.targetY * follow + self.poseY * (1-follow)
    local k = 1 - math.exp(-7.5 * seconds)
    self.x += (targetX-self.x)*k
    self.y += (targetY-self.y)*k
    if self.lookX ~= nil then self.lookX.value = self.x end
    if self.lookY ~= nil then self.lookY.value = self.y end
    return true
end

return function(context: Context): Layout<Gaze>
    return {
        lookX=nil, lookY=nil, follow=1, poseX=0, poseY=0,
        x=0, y=0, targetX=0, targetY=0,
        size=Vector.xy(1180,780),
        init=init, resize=resize, advance=advance, pointerMove=pointerMove, pointerExit=pointerExit,
    }
end
