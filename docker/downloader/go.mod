// The downloader deliberately has no dependencies.
//
// It is a byte mover with a security boundary, and every module it pulled in
// would be a module that could reach the network from inside the one container
// allowed to dial arbitrary CDN hosts. The standard library covers all of it:
// net/http for the transfers, crypto/sha256 for verification, and net for the
// address checks that make the SSRF guard real.
module dtk/downloader

go 1.23
