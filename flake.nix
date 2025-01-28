{
  description = "a port of macOS MenuMeters to QT";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs";

  outputs = {
    self,
    nixpkgs,
  }: let
    system = "x86_64-linux";
    pkgs = import nixpkgs {inherit system;};
  in {
    packages.x86_64-linux.default = pkgs.python310Packages.buildPythonApplication {
      pname = "menumeters-qt";
      version = "0.1.0";
      src = self;

      propagatedBuildInputs = [
        pkgs.python310Packages.psutil
        pkgs.python310Packages.pyqt6
      ];
    };

    devShells.x86_64-linux.default = pkgs.mkShell {
      buildInputs = [
        pkgs.python310Packages.psutil
        pkgs.python310Packages.pyqt6
      ];
    };
  };
}
