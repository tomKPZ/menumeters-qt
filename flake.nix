{
  description = "a port of macOS MenuMeters to QT";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";

  outputs = { self, nixpkgs, ... }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; };
    in
    {
      packages.${system}.default = pkgs.python3Packages.buildPythonApplication {
        pname = "menumeters-qt";
        version = "0.1.0";
        src = self;
        propagatedBuildInputs = with pkgs.python3Packages; [
          psutil
          pyqt6
        ];
      };

      devShells.${system}.default = pkgs.mkShell {
        buildInputs = [
          (pkgs.python3.withPackages (p: with p; [
            psutil
            pyqt6
          ]))
        ];
      };
    };
}
