package com.emma.robot.control;

import java.io.BufferedInputStream;
import java.io.ByteArrayOutputStream;
import java.io.Closeable;
import java.io.EOFException;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.Base64;
import java.util.Locale;

import javax.net.ssl.HostnameVerifier;
import javax.net.ssl.SSLSocket;
import javax.net.ssl.SSLSocketFactory;

/**
 * A minimal RFC 6455 client for /ws/robot-doa: text frames out (masked, as
 * a client must), text/ping/close in. The app has no WebSocket library and
 * one socket of small JSON does not justify adding one; everything else in
 * the app talks HTTP.
 *
 * Pure Java (java.net + javax.net.ssl, no android.*): the tests compile it
 * with the JDK and run it against the REAL endpoint (uvicorn, the same
 * handler as main.py), so the handshake and the framing are exercised, not
 * read. TLS: the platform's default SSLSocketFactory -- on Android that is
 * the app's network security config, so the robot trusts exactly the CA the
 * WebView trusts (res/raw/emma_local_ca) -- and an explicit HostnameVerifier,
 * because a raw SSLSocket verifies NO hostname by itself (Android's own
 * warning about SSLSocket). ArrayListener passes the platform default.
 *
 * Sends are synchronized; one reader thread calls readText().
 */
final class DoaLink implements Closeable {
    private static final String GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11";
    /** Bigger than anything the server sends (ready ~200 bytes). */
    private static final int MAX_MESSAGE = 64 * 1024;
    private static final int MAX_HEADER = 8 * 1024;

    private final Socket socket;
    private final InputStream in;
    private final OutputStream out;
    private final SecureRandom random = new SecureRandom();
    private volatile boolean closed = false;

    private DoaLink(Socket socket) throws IOException {
        this.socket = socket;
        this.in = new BufferedInputStream(socket.getInputStream());
        this.out = socket.getOutputStream();
    }

    /**
     * Connect and upgrade. {@code url} is ws://, wss://, http:// or https://
     * (http(s) maps to ws(s)); the caller decides which schemes it allows.
     *
     * @param verifier checks the TLS peer for wss; ignored for ws
     */
    static DoaLink open(String url, String userAgent, int timeoutMs, HostnameVerifier verifier)
            throws IOException {
        URI uri = URI.create(url);
        String scheme = uri.getScheme() == null ? "" : uri.getScheme().toLowerCase(Locale.ROOT);
        boolean tls = scheme.equals("wss") || scheme.equals("https");
        if (!tls && !scheme.equals("ws") && !scheme.equals("http")) throw new IOException("bad scheme " + scheme);
        String host = uri.getHost();
        if (host == null) throw new IOException("no host");
        int port = uri.getPort() > 0 ? uri.getPort() : (tls ? 443 : 80);
        Socket raw = new Socket();
        Socket socket = raw;
        try {
            raw.connect(new InetSocketAddress(host, port), timeoutMs);
            raw.setSoTimeout(timeoutMs);
            raw.setTcpNoDelay(true);
            if (tls) {
                SSLSocketFactory factory = (SSLSocketFactory) SSLSocketFactory.getDefault();
                SSLSocket ssl = (SSLSocket) factory.createSocket(raw, host, port, true);
                socket = ssl;
                ssl.startHandshake();
                if (verifier == null || !verifier.verify(host, ssl.getSession())) {
                    throw new IOException("TLS peer is not " + host);
                }
            }
            DoaLink link = new DoaLink(socket);
            link.handshake(host, port, tls, uri.getRawPath(), uri.getRawQuery(), userAgent);
            return link;
        } catch (IOException | RuntimeException e) {
            try {
                socket.close();
            } catch (IOException ignored) {
                // already failing
            }
            if (e instanceof IOException) throw (IOException) e;
            throw new IOException(e);
        }
    }

    private void handshake(String host, int port, boolean tls, String path, String query, String agent)
            throws IOException {
        byte[] nonce = new byte[16];
        random.nextBytes(nonce);
        String key = Base64.getEncoder().encodeToString(nonce);
        boolean defaultPort = (tls && port == 443) || (!tls && port == 80);
        String hostHeader = (host.indexOf(':') >= 0 ? "[" + host + "]" : host) + (defaultPort ? "" : ":" + port);
        String target = (path == null || path.isEmpty() ? "/" : path) + (query == null ? "" : "?" + query);
        String request = "GET " + target + " HTTP/1.1\r\n"
                + "Host: " + hostHeader + "\r\n"
                + "Upgrade: websocket\r\n"
                + "Connection: Upgrade\r\n"
                + "Sec-WebSocket-Key: " + key + "\r\n"
                + "Sec-WebSocket-Version: 13\r\n"
                + "User-Agent: " + agent + "\r\n"
                + "\r\n";
        out.write(request.getBytes(StandardCharsets.ISO_8859_1));
        out.flush();
        String head = readHead();
        String[] lines = head.split("\r\n");
        if (lines.length == 0 || !lines[0].matches("HTTP/1\\.[01] 101( .*)?")) {
            throw new IOException("upgrade refused: " + (lines.length == 0 ? "" : lines[0]));
        }
        String expected;
        try {
            MessageDigest sha1 = MessageDigest.getInstance("SHA-1");
            expected = Base64.getEncoder().encodeToString(
                    sha1.digest((key + GUID).getBytes(StandardCharsets.ISO_8859_1)));
        } catch (Exception e) {
            throw new IOException("no SHA-1", e);
        }
        String accept = null;
        for (int k = 1; k < lines.length; k++) {
            int colon = lines[k].indexOf(':');
            if (colon > 0 && lines[k].substring(0, colon).trim().equalsIgnoreCase("Sec-WebSocket-Accept")) {
                accept = lines[k].substring(colon + 1).trim();
            }
        }
        if (!expected.equals(accept)) throw new IOException("bad Sec-WebSocket-Accept");
    }

    private String readHead() throws IOException {
        ByteArrayOutputStream head = new ByteArrayOutputStream();
        int state = 0;
        while (state < 4) {
            int b = in.read();
            if (b < 0) throw new EOFException("closed during upgrade");
            head.write(b);
            if (head.size() > MAX_HEADER) throw new IOException("upgrade response too long");
            state = (b == '\r' && (state == 0 || state == 2)) || (b == '\n' && (state == 1 || state == 3))
                    ? state + 1 : (b == '\r' ? 1 : 0);
        }
        return new String(head.toByteArray(), StandardCharsets.ISO_8859_1);
    }

    /** Read timeout for readText (the server beats every 15 s). */
    void setReadTimeout(int ms) throws IOException {
        socket.setSoTimeout(ms);
    }

    void sendText(String text) throws IOException {
        sendFrame(0x1, text.getBytes(StandardCharsets.UTF_8));
    }

    /** One binary message (ARRAY_AUDIO's beam chunks, the channels 4-7 test):
     *  masked like every client frame; the server checks the header. */
    void sendBinary(byte[] payload) throws IOException {
        sendFrame(0x2, payload);
    }

    private synchronized void sendFrame(int opcode, byte[] payload) throws IOException {
        if (closed && opcode != 0x8) throw new IOException("closed");
        int n = payload.length;
        byte[] header = new byte[14];
        int h = 0;
        header[h++] = (byte) (0x80 | opcode);
        if (n < 126) {
            header[h++] = (byte) (0x80 | n);
        } else if (n < 65536) {
            header[h++] = (byte) (0x80 | 126);
            header[h++] = (byte) (n >>> 8);
            header[h++] = (byte) n;
        } else {
            header[h++] = (byte) (0x80 | 127);
            for (int s = 56; s >= 0; s -= 8) header[h++] = (byte) (((long) n) >>> s);
        }
        byte[] mask = new byte[4];
        random.nextBytes(mask);
        System.arraycopy(mask, 0, header, h, 4);
        h += 4;
        byte[] body = new byte[n];
        for (int k = 0; k < n; k++) body[k] = (byte) (payload[k] ^ mask[k & 3]);
        out.write(header, 0, h);
        out.write(body);
        out.flush();
    }

    /** The next text message, or null when the server closed. Answers pings
     *  itself; binary and pongs are skipped. */
    String readText() throws IOException {
        ByteArrayOutputStream message = null;
        while (true) {
            int b0 = in.read();
            int b1 = in.read();
            if (b0 < 0 || b1 < 0) return null;
            boolean fin = (b0 & 0x80) != 0;
            int opcode = b0 & 0x0f;
            boolean masked = (b1 & 0x80) != 0;
            long length = b1 & 0x7f;
            if (length == 126) {
                length = (readByte() << 8) | readByte();
            } else if (length == 127) {
                length = 0;
                for (int k = 0; k < 8; k++) length = (length << 8) | readByte();
            }
            if (length < 0 || length > MAX_MESSAGE) throw new IOException("frame too long");
            byte[] mask = null;
            if (masked) {
                mask = new byte[4];
                readFully(mask);
            }
            byte[] payload = new byte[(int) length];
            readFully(payload);
            if (mask != null) for (int k = 0; k < payload.length; k++) payload[k] ^= mask[k & 3];
            if (opcode == 0x8) {                 // close: answer it and stop
                try {
                    sendFrame(0x8, payload.length >= 2 ? new byte[]{payload[0], payload[1]} : new byte[0]);
                } catch (IOException ignored) {
                    // the peer may already be gone
                }
                closed = true;
                return null;
            }
            if (opcode == 0x9) {                 // ping
                sendFrame(0xA, payload);
                continue;
            }
            if (opcode == 0xA) continue;         // pong
            if (opcode == 0x1 || (opcode == 0x0 && message != null)) {
                if (message == null) message = new ByteArrayOutputStream();
                message.write(payload);
                if (message.size() > MAX_MESSAGE) throw new IOException("message too long");
                if (fin) return new String(message.toByteArray(), StandardCharsets.UTF_8);
                continue;
            }
            message = null;                      // binary (or a stray continuation): not ours
        }
    }

    private int readByte() throws IOException {
        int b = in.read();
        if (b < 0) throw new EOFException();
        return b;
    }

    private void readFully(byte[] buffer) throws IOException {
        int at = 0;
        while (at < buffer.length) {
            int n = in.read(buffer, at, buffer.length - at);
            if (n < 0) throw new EOFException();
            at += n;
        }
    }

    boolean isClosed() {
        return closed;
    }

    /** Best-effort close frame, then the socket; unblocks the reader. */
    @Override
    public void close() {
        if (!closed) {
            closed = true;
            try {
                sendFrame(0x8, new byte[]{0x03, (byte) 0xE8});   // 1000 normal
            } catch (IOException ignored) {
                // closing anyway
            }
        }
        try {
            socket.close();
        } catch (IOException ignored) {
            // nothing left to do
        }
    }
}
