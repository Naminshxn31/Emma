// Reconstructed from installed Aobo v278 Stub/Proxy bytecode, 2026-09-11.
// Reference only: not compiled, bound, or executed against the robot.
// Preserve declaration order: Binder transactions 1, 2, 3. Not oneway.
package com.helang.lib;

import com.helang.lib.IMyAidlCallBackInterface;

interface IMyAidlInterface {
    void sendMessage(String tag, String message);
    void registerListener(IMyAidlCallBackInterface listener);
    void unregisterListener(IMyAidlCallBackInterface listener);
}
